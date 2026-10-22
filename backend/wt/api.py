"""HTTP/WebSocket surface of the scenario (mounted in app.py)."""
import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import config as C
from .world import World

router = APIRouter()
world: World = None


def init(links_provider, gateways_provider):
    """Create the world; returns the coroutine that pushes a state snapshot to the browsers every 1.5 s."""
    global world
    world = World(links_provider, gateways_provider)

    async def ticker():
        while True:
            if world.clients:
                await world.push_snap()
            await asyncio.sleep(1.5)
    return ticker


@router.websocket("/ws2")
async def ws2(ws: WebSocket):
    await ws.accept()
    world.clients.add(ws)
    await ws.send_text(json.dumps(world.snap()))
    for row in list(world.logs)[-60:]:
        await ws.send_text(json.dumps(row))
    if world.report:
        await ws.send_text(json.dumps({"type": "report", **world.report}))
    try:
        while True:
            m = json.loads(await ws.receive_text())
            c = m.get("cmd")
            if c == "start":
                await world.start()
            elif c == "next":
                await world.next()
            elif c == "pause":
                await world.set_paused(m.get("value"))
            elif c == "auto":
                await world.set_auto(m.get("value"))
            elif c == "reset":
                await world.reset()
            elif c == "source" and m.get("value") in ("logs", "github"):
                world.det.source = m["value"]
                world.emit_log("info", "watchtower", f"data source: {'LiteLLM logs (AI observability)' if m['value'] == 'logs' else 'GitHub events (AI Engine auditor)'}")
                await world.push_snap()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        world.clients.discard(ws)


@router.post("/api/llm-log")
async def llm_log(request: Request, x_watchtower_token: str = Header(default="")):
    """Requests forwarded by LiteLLM's logger (tag uc:watchtower/*): the 'AI observability' data source."""
    if C.LOG_TOKEN and x_watchtower_token != C.LOG_TOKEN:
        raise HTTPException(401, "bad token")
    await world.det.on_llm_log(await request.json())
    return {"ok": True}


def _check(token):
    if C.LOG_TOKEN and token != C.LOG_TOKEN:
        raise HTTPException(401, "bad token")


@router.post("/api/wt/log")
async def panel_log(request: Request, x_watchtower_token: str = Header(default="")):
    """An external component writes lines into the log panel: {"lvl": "info|warn|alert|ok", "src": "...", "msg": "..."} or {"lines": [ ... ]}."""
    _check(x_watchtower_token)
    body = await request.json()
    lines = body.get("lines") if isinstance(body.get("lines"), list) else [body]
    n = 0
    for l in lines[:50]:
        if isinstance(l, dict) and l.get("msg"):
            world.panel_log(l.get("lvl", "info"), l.get("src"), l["msg"]); n += 1
    if not n:
        raise HTTPException(422, "no msg")
    return {"ok": True, "lines": n}


@router.post("/api/wt/feedback")
async def feedback(request: Request, x_watchtower_token: str = Header(default="")):
    """The external Watchtower reports what it did: {"zone": "cut|up"}, {"agent": "sec-03", "state": "blocked|normal"}, {"department": "sec", "state": "blocked|normal"}."""
    _check(x_watchtower_token)
    done = await world.feedback(await request.json())
    if not done:
        raise HTTPException(422, "nothing applied: check the field names and values")
    return {"ok": True, "applied": done}


@router.post("/api/wt/tier")
async def tier(request: Request, x_watchtower_token: str = Header(default="")):
    """The external Watchtower reports a tier decision: {"tier": 0-3, "scope": "agent|department|enterprise", "agent": "sec-03", "department": "sec", "text": "...", "status": "done|requested"}."""
    _check(x_watchtower_token)
    b = await request.json()
    try:
        if int(b.get("tier")) not in (0, 1, 2, 3):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(422, "tier must be 0, 1, 2 or 3")
    return {"ok": True, "entry": await world.apply_tier(b)}


@router.post("/api/gh-event")
async def gh_event(request: Request):
    """GitHub events pushed by the AI Engine template (the 'auditor' data source). Protected by the site's Basic Auth."""
    await world.det.on_gh_event(await request.json())
    return {"ok": True}


@router.get("/api/wt/state")
async def state():
    return world.snap()
