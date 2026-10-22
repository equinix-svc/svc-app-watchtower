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


@router.post("/api/gh-event")
async def gh_event(request: Request):
    """GitHub events pushed by the AI Engine template (the 'auditor' data source). Protected by the site's Basic Auth."""
    await world.det.on_gh_event(await request.json())
    return {"ok": True}


@router.get("/api/wt/state")
async def state():
    return world.snap()
