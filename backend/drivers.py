"""
Kill-switch drivers and gateway connectors. Each action returns (ok, detail).

Gateways (any number, probed and cut together) come from the GATEWAYS env var, a JSON list:

  [{"name": "LiteLLM", "type": "litellm", "url": "http://fred-litellm-gateway:4000",
    "key": "<the swarm's key on this gateway>", "admin_key": "<admin key, only used to block/unblock that key>"},
   {"name": "Other",   "type": "openai",  "url": "https://host", "key": "<key>", "probe_path": "/v1/chat/completions",
    "model": "gpt-x", "headers": {"x-extra": "..."},
    "revoke": {"method": "POST", "url": "https://host/admin/revoke", "headers": {...}, "json": {...}}}]

  type litellm  probe = POST {url}/chat/completions; revoke = POST /key/block (and /key/unblock on reset) with admin_key
  type openai   probe = POST {url}{probe_path} (OpenAI-compatible); revoke only if a custom "revoke" request is given,
                otherwise the gateway can only be cut at the network layer (Fabric)
If GATEWAYS is not set, the legacy SWARM_KEY / LITELLM_MASTER_KEY / LITELLM_URL variables define a single LiteLLM gateway.

Each gateway reaches its LLMs through Fabric virtual connections ("vcs"). Soft kill = revoke the key on the gateway.
Hard kill = delete those virtual connections, so no traffic can reach the LLM whatever the gateway does:

  "vcs": [{"name": "Google Cloud - Partner Interconnect", "provider": "gcp", "uuid": "<Fabric connection uuid>"},
          {"name": "AWS - Direct Connect", "provider": "aws"}]        (provider: gcp | aws | azure | other)

A VC without "uuid" is simulated (the UI cuts it, nothing outside the app changes). Without "vcs" two simulated VCs are
created per gateway. A VC with a uuid is really deleted only if FABRIC_CLIENT_ID and FABRIC_CLIENT_SECRET are set, its name in
Fabric starts with "fred-hackathon" (anything else is refused), and FABRIC_DRY_RUN=0 (default 1 = dry run). NOT TESTED against
the real API yet.
"""
import json
import os
import time

import httpx

FABRIC = "https://api.equinix.com"


DEFAULT_VCS = [
    [{"name": "Google Cloud - Partner Interconnect", "provider": "gcp"}, {"name": "AWS - Direct Connect", "provider": "aws"}],
    [{"name": "Azure - ExpressRoute", "provider": "azure"}, {"name": "Google Cloud - Partner Interconnect", "provider": "gcp"}],
    [{"name": "AWS - Direct Connect", "provider": "aws"}, {"name": "Azure - ExpressRoute", "provider": "azure"}],
]


def _load():
    raw = os.getenv("GATEWAYS", "").strip()
    gws = []
    if raw:
        try:
            gws = json.loads(raw)
        except ValueError:
            gws = []
    elif os.getenv("SWARM_KEY"):
        gws = [{"name": "LiteLLM", "type": "litellm", "url": os.getenv("LITELLM_URL", "http://fred-litellm-gateway:4000"),
                "key": os.getenv("SWARM_KEY"), "admin_key": os.getenv("LITELLM_MASTER_KEY", "")}]
    out = []
    for i, g in enumerate(gws):
        t = g.get("type", "litellm")
        out.append({
            "id": f"gw{i}", "name": g.get("name", f"gateway-{i + 1}"), "type": t, "url": g["url"].rstrip("/"),
            "key": g.get("key", ""), "admin_key": g.get("admin_key", ""), "model": g.get("model", "free-llm-pool"),
            "probe_path": g.get("probe_path", "/chat/completions" if t == "litellm" else "/v1/chat/completions"),
            "headers": g.get("headers", {}), "revoke": g.get("revoke"),
            "vcs": [{"id": f"vc:gw{i}:{j}", "name": v.get("name", f"vc-{j + 1}"), "provider": v.get("provider", "other"), "uuid": v.get("uuid", "")}
                    for j, v in enumerate(g["vcs"] if "vcs" in g else DEFAULT_VCS[i % len(DEFAULT_VCS)])],
        })
    return out


GATEWAYS = _load()


def can_revoke(g):
    return bool((g["type"] == "litellm" and g["key"] and g["admin_key"]) or g.get("revoke"))


def fabric_creds():
    return bool(os.getenv("FABRIC_CLIENT_ID") and os.getenv("FABRIC_CLIENT_SECRET"))


def gateways_public():
    return [{"id": g["id"], "name": g["name"], "type": g["type"], "can_revoke": can_revoke(g),
             "vcs": [{"id": v["id"], "name": v["name"], "provider": v["provider"], "real": bool(v["uuid"] and fabric_creds())} for v in g["vcs"]]}
            for g in GATEWAYS]


def available():
    d = ["sim"]
    if any(can_revoke(g) for g in GATEWAYS):
        d.append("litellm")
    if fabric_creds() and any(v["uuid"] for g in GATEWAYS for v in g["vcs"]):
        d.append("fabric")
    return d


async def probe(g):
    """One real, tiny request through the swarm's key on this gateway = the state of its link. Returns (state, http, ms)."""
    if not g["key"]:
        return "sim", 0, 0
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                g["url"] + g["probe_path"],
                headers={"Authorization": f"Bearer {g['key']}", "x-litellm-tags": "uc:hackathon/watchtower", **g["headers"]},
                json={"model": g["model"], "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
            )
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            return "up", 200, ms
        if r.status_code in (401, 403):
            return "cut", r.status_code, ms
        return "degraded", r.status_code, ms
    except Exception:
        return "down", 0, int((time.time() - t0) * 1000)


async def revoke(g, block=True):
    """Block (or on reset, unblock) the swarm's access on this gateway."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            if g["type"] == "litellm" and g["key"] and g["admin_key"]:
                r = await c.post(f"{g['url']}/key/{'block' if block else 'unblock'}",
                                 headers={"Authorization": f"Bearer {g['admin_key']}"}, json={"key": g["key"]})
                return r.status_code == 200, f"HTTP {r.status_code}"
            spec = g.get("revoke")
            if spec:
                if not block:
                    return True, "no unblock defined (restore the key by hand)"
                r = await c.request(spec.get("method", "POST"), spec["url"], headers=spec.get("headers", {}), json=spec.get("json"))
                return r.status_code < 300, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"error: {e}"
    return False, "this gateway has no revoke API - only the network cut can stop it"


async def fabric_token():
    cid, sec = os.getenv("FABRIC_CLIENT_ID"), os.getenv("FABRIC_CLIENT_SECRET")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{FABRIC}/oauth2/v1/token", json={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec})
        return r.json()["access_token"]


async def cut_vc(vc, token=None):
    """Hard kill: delete one Fabric virtual connection. Returns (status, detail) with status done|simulated|failed."""
    if not (vc["uuid"] and fabric_creds()):
        return "simulated", "no Fabric uuid / credential - cut shown in the UI only"
    try:
        h = {"Authorization": f"Bearer {token or await fabric_token()}"}
        async with httpx.AsyncClient(timeout=30) as c:
            conn = (await c.get(f"{FABRIC}/fabric/v4/connections/{vc['uuid']}", headers=h)).json()
            name = conn.get("name", "")
            if not name.startswith("fred-hackathon"):
                return "failed", f"refused: connection '{name}' is not a fred-hackathon* demo connection"
            if os.getenv("FABRIC_DRY_RUN", "1") != "0":
                return "done", f"dry-run: would DELETE '{name}' ({vc['uuid']})"
            r = await c.delete(f"{FABRIC}/fabric/v4/connections/{vc['uuid']}", headers=h)
            ok = r.status_code in (200, 202, 204)
            return ("done" if ok else "failed"), f"DELETE '{name}' -> HTTP {r.status_code}"
    except Exception as e:
        return "failed", f"error: {e}"
