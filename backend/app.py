"""
Watchtower demo server: runs the simulation, streams it to the browser over a WebSocket, and executes the kill switch.

  The 3D frontend (hackathon-app) is served by nginx from fred-litellm-gateway/frontend/hackathon-app;
  nginx proxies /demos/hackathon-app/ws and /demos/hackathon-app/api/ to this service.
  WS   /ws          server -> client: {"type":"tick",...} 4x/s, {"type":"mitigation",...}, {"type":"link",...}
                    client -> server: {"cmd":"start","scenario":"swarm|runaway|baseline","count":5..100,"pace":agents_per_second,"armed":true}
                                      {"cmd":"kill"} {"cmd":"reset"} {"cmd":"armed","value":true}
"""
import asyncio
import json
import os
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import drivers
from sim import TICK, Sim

app = FastAPI(title="Watchtower")

sim = Sim()
clients = set()
state = {"armed": True, "links": {g["id"]: {"state": "up", "http": 0, "ms": 0} for g in drivers.GATEWAYS}, "mitigation": None, "running": False}


def overall():
    st = [l["state"] for l in state["links"].values()] or ["up"]
    return "cut" if all(x == "cut" for x in st) else "down" if any(x == "down" for x in st) else "degraded" if any(x == "degraded" for x in st) else "up"


async def broadcast(msg):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def new_mitigation(manual):
    steps = [{"id": "freeze", "label": "Quarantine agents", "status": "pending", "detail": ""}]
    steps += [{"id": "revoke:" + g["id"], "label": f"Soft kill - revoke key: {g['name']}", "status": "pending", "detail": ""} for g in drivers.GATEWAYS]
    steps += [{"id": v["id"], "label": f"Hard kill - cut Fabric VC: {v['name']} ({g['name'][-6:]})", "status": "pending", "detail": ""}
              for g in drivers.GATEWAYS for v in g["vcs"]]
    return {"manual": manual, "started": sim.t, "steps": steps, "done": False}


async def set_step(m, sid, status, detail=""):
    for s in m["steps"]:
        if s["id"] == sid:
            s["status"], s["detail"] = status, detail
    await broadcast({"type": "mitigation", "m": m})


async def run_mitigation(manual=False):
    if sim.incident:
        return
    sim.incident = True
    sim.t_cut = sim.t
    sim.at_cut = {"agents": len(sim.agents), "secret_reads": sim.totals["secret_reads"], "llm_calls": sim.totals["llm_calls"]}
    m = state["mitigation"] = new_mitigation(manual)
    sim.log("alert", "watchtower", f"INCIDENT declared ({'manual kill' if manual else f'score {sim.score}, CRITICAL sustained'}) - executing kill switch")
    await broadcast({"type": "mitigation", "m": m})
    sim.freeze_all()                                  # instant: nothing may act while the cut is executed
    await asyncio.sleep(0.4)
    await set_step(m, "freeze", "done", f"{len(sim.agents)} agents frozen")
    await asyncio.sleep(0.6)
    async def cut_gateway(g):
        sid = "revoke:" + g["id"]
        if not drivers.can_revoke(g):
            await set_step(m, sid, "skipped", "no revoke API on this gateway - only the network cut can stop it")
            return
        await set_step(m, sid, "running")
        ok, d = await drivers.revoke(g, True)
        if ok:
            st, code, ms = await drivers.probe(g)           # prove it: the swarm key must now be rejected
            state["links"][g["id"]] = {"state": st, "http": code, "ms": ms}
            ok = st == "cut"
            d = f"key blocked, verification request -> HTTP {code}" + ("" if ok else " (still accepted!)")
        await set_step(m, sid, "done" if ok else "failed", d)
        await broadcast({"type": "link", "gw": g["id"], **state["links"][g["id"]]})

    await asyncio.gather(*(cut_gateway(g) for g in drivers.GATEWAYS))
    await asyncio.sleep(0.6)
    # hard kill: every Fabric VC behind every gateway, concurrently
    vcs = [(g, v) for g in drivers.GATEWAYS for v in g["vcs"]]
    token = None
    if any(v["uuid"] for _, v in vcs) and drivers.fabric_creds():
        try:
            token = await drivers.fabric_token()
        except Exception:
            token = None

    async def cut_one(g, v):
        await set_step(m, v["id"], "running")
        status, d = await drivers.cut_vc(v, token)
        v["_status"] = status
        await set_step(m, v["id"], status, d)

    await asyncio.gather(*(cut_one(g, v) for g, v in vcs))
    for g in drivers.GATEWAYS:                          # a gateway with no revoke API is only stopped once its VCs are cut
        ok = g["vcs"] and all(v.get("_status") in ("done", "simulated") for v in g["vcs"])
        if not drivers.can_revoke(g) and ok:
            state["links"][g["id"]] = {"state": "cut", "http": 0, "ms": 0, "sim": all(v.get("_status") == "simulated" for v in g["vcs"])}
            await broadcast({"type": "link", "gw": g["id"], **state["links"][g["id"]]})
    m["done"] = True
    m["summary"] = sim.summary()
    sim.log("ok", "watchtower", f"contained: {m['summary']['agents_at_cut']} of {sim.target} agents, LLM path cut at T+{m['summary']['t_cut']}s")
    await broadcast({"type": "mitigation", "m": m})


async def do_reset():
    sim.reset()
    state["mitigation"] = None
    state["running"] = False
    await asyncio.gather(*(drivers.revoke(g, False) for g in drivers.GATEWAYS if drivers.can_revoke(g)))
    for k in state["links"]:
        state["links"][k] = {"state": "up", "http": 0, "ms": 0}
    await broadcast({"type": "reset"})


async def tick_loop():
    while True:
        t0 = time.time()
        if state["running"] or sim.scenario != "idle":
            snap = sim.step()
        else:
            snap = sim.snapshot()
        snap.update(type="tick", armed=state["armed"], link=overall())
        if clients:
            await broadcast(snap)
        if state["armed"] and sim.should_cut and not sim.incident:
            asyncio.create_task(run_mitigation(False))
        await asyncio.sleep(max(0.0, TICK - (time.time() - t0)))


async def probe_loop():
    while True:
        if clients and not sim.incident and drivers.GATEWAYS:
            res = await asyncio.gather(*(drivers.probe(g) for g in drivers.GATEWAYS))
            for g, (st, code, ms) in zip(drivers.GATEWAYS, res):
                state["links"][g["id"]] = {"state": "up" if st == "sim" else st, "http": code, "ms": ms}
                await broadcast({"type": "link", "gw": g["id"], **state["links"][g["id"]]})
        await asyncio.sleep(4)


@app.on_event("startup")
async def startup():
    asyncio.create_task(tick_loop())
    asyncio.create_task(probe_loop())
    await asyncio.gather(*(drivers.revoke(g, False) for g in drivers.GATEWAYS if drivers.can_revoke(g)))   # start with every swarm key usable


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps({"type": "hello", "drivers": drivers.available(), "gateways": drivers.gateways_public(), "links": state["links"], "armed": state["armed"]}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            if cmd == "start":
                if sim.incident or state["mitigation"]:
                    await do_reset()
                state["armed"] = bool(msg.get("armed", True))
                sim.start(msg.get("scenario", "swarm"), msg.get("count", 60), msg.get("pace"))
                state["running"] = True
                await broadcast({"type": "started", "scenario": sim.scenario, "target": sim.target, "armed": state["armed"]})
            elif cmd == "kill":
                if sim.scenario != "idle" and not sim.incident:
                    asyncio.create_task(run_mitigation(True))
            elif cmd == "reset":
                await do_reset()
            elif cmd == "armed":
                state["armed"] = bool(msg.get("value"))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(ws)


@app.get("/api/health")
async def health():
    return {"ok": True, "drivers": drivers.available(), "gateways": drivers.gateways_public(), "clients": len(clients)}
