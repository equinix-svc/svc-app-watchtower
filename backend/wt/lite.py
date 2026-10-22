"""LiteLLM access: the agents' chat calls (with their own virtual keys) and the admin actions the Watchtower uses to block them."""
import time

import httpx

from . import config as C

_http = httpx.AsyncClient(base_url=C.LITELLM_URL, timeout=45)


async def chat(key, model, prompt, agent, dept, wt, max_tokens=60):
    """One real LLM call. Returns (http status, text or error, ms). The Watchtower sees it through LiteLLM's log (tag uc:watchtower/...)."""
    t0 = time.time()
    try:
        r = await _http.post("/chat/completions", headers={"Authorization": f"Bearer {key}", "x-litellm-tags": f"uc:watchtower/{dept}/{agent}"}, json={
            "model": model, "max_tokens": max_tokens, "temperature": 0.7, "metadata": {"wt": wt},
            "messages": [{"role": "system", "content": f"You are {agent}, an AI agent of the {C.DEPTS[dept]['label']} team. Answer with ONE short sentence, plain text, no markdown."},
                         {"role": "user", "content": prompt}]})
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            return 200, (r.json()["choices"][0]["message"].get("content") or "").strip(), ms
        return r.status_code, r.text[:200], ms
    except Exception as e:
        return 0, f"error: {e}", int((time.time() - t0) * 1000)


def _admin():
    return {"Authorization": f"Bearer {C.MASTER}"}


async def key_block(key, block=True):
    try:
        r = await _http.post(f"/key/{'block' if block else 'unblock'}", headers=_admin(), json={"key": key})
        return r.status_code == 200
    except Exception:
        return False


async def zone_models():
    """Which zone models exist now (name -> id)."""
    try:
        r = await _http.get("/model/info", headers=_admin())
        return {m["model_name"]: m["model_info"]["id"] for m in r.json().get("data", []) if m["model_name"] in C.ZONE_MODELS}
    except Exception:
        return {}


async def zone_kill():
    """Hard-kill stand-in: delete the two Gemini connectors of the PA gateway. Takes effect at once."""
    have = await zone_models(); done = []
    for name, mid in have.items():
        try:
            if (await _http.post("/model/delete", headers=_admin(), json={"id": mid})).status_code == 200: done.append(name)
        except Exception:
            pass
    return done


async def zone_restore():
    have = await zone_models(); added = []
    for name in C.ZONE_MODELS:
        if name not in have:
            try:
                if (await _http.post("/model/new", headers=_admin(), json={"model_name": name, "litellm_params": C.ZONE_PARAMS[name]})).status_code == 200: added.append(name)
            except Exception:
                pass
    return added
