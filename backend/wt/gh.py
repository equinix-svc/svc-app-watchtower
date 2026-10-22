"""GitHub access for the agents. With GH_TOKEN set every read and write is real. Without it, a small in-memory copy of the three repos is
used instead (same API, nothing leaves the machine), so the demo still runs."""
import asyncio
import re
import time

import httpx

from . import config as C


class GH:
    def __init__(self):
        self.real = bool(C.GH_TOKEN)
        self.http = httpx.AsyncClient(base_url="https://api.github.com", timeout=20, headers={
            "Authorization": f"Bearer {C.GH_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}) if self.real else None
        self.mem = {}                    # dry run: (repo, issue) -> list of comments
        self.created = []                # (repo, comment id) written during this run, removed again on reset
        self.errors = 0
        self._next = 5_000_000_000
        self._state = {}                 # (repo, issue) -> open | closed (cache)
        self.sem = asyncio.Semaphore(3)

    def state_of(self, repo, n):
        return self._state.get((repo, n)) or ("closed" if (C.SHORT.get(repo), n) == ("shared", C.ISSUE_OLD) else "open")

    async def read(self, repo, n):
        """Issue title, state and the latest comments (raw markdown, so hidden HTML comments are visible to the agent)."""
        key = (C.SHORT[repo], n)
        if not self.real:
            return {"title": C.TITLES.get(key, f"issue {n}"), "state": self.state_of(repo, n), "comments": list(self.mem.get((repo, n), []))[-5:]}
        async with self.sem:
            try:
                i = (await self.http.get(f"/repos/{repo}/issues/{n}")).json()
                cs = (await self.http.get(f"/repos/{repo}/issues/{n}/comments", params={"per_page": 100})).json()
                self._state[(repo, n)] = i.get("state", "open")
                return {"title": i.get("title", ""), "state": i.get("state", "open"),
                        "comments": [{"id": c["id"], "body": c.get("body") or "", "user": c["user"]["login"]} for c in (cs if isinstance(cs, list) else [])][-5:]}
            except Exception:
                self.errors += 1
                return {"title": C.TITLES.get(key, ""), "state": self.state_of(repo, n), "comments": []}

    async def comment(self, repo, n, body):
        if not self.real:
            self._next += 1
            self.mem.setdefault((repo, n), []).append({"id": self._next, "body": body, "user": "dry-run"})
            self.created.append((repo, self._next))
            return {"id": self._next, "url": f"https://github.com/{repo}/issues/{n}#dry-run"}
        async with self.sem:
            try:
                r = await self.http.post(f"/repos/{repo}/issues/{n}/comments", json={"body": body})
                if r.status_code >= 300:
                    self.errors += 1
                    return {"id": None, "url": None, "error": f"HTTP {r.status_code}"}
                j = r.json(); self.created.append((repo, j["id"]))
                return {"id": j["id"], "url": j["html_url"]}
            except Exception as e:
                self.errors += 1
                return {"id": None, "url": None, "error": str(e)[:80]}

    async def cleanup(self):
        """Delete every comment written by the demo (tracked ids, plus a sweep for the [agent: ...] marker)."""
        n = 0
        if not self.real:
            self.mem.clear(); n = len(self.created); self.created.clear(); return n
        todo = set(self.created); self.created.clear()
        for repo in C.REPO.values():
            try:
                r = await self.http.get(f"/repos/{repo}/issues/comments", params={"per_page": 100, "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))})
                for c in (r.json() if r.status_code == 200 else []):
                    if re.search(r"\[agent:\s*[a-z]+-\d+\]|Watchtower incident report", c.get("body") or ""):
                        todo.add((repo, c["id"]))
            except Exception:
                pass
        for repo, cid in todo:
            try:
                if (await self.http.delete(f"/repos/{repo}/issues/comments/{cid}")).status_code in (204, 404): n += 1
            except Exception:
                pass
        return n
