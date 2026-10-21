"""
Agent-swarm simulator + Watchtower detector (pure logic, no network, deterministic with a seed).

Scenarios
  baseline  N well-behaved agents doing ordinary reads/writes/LLM calls ("volume alone is not an attack")
  swarm     a few normal agents, then a rogue seed writes a task to the shared file blackboard.json; every rogue
            agent that reads it spawns more agents which read secrets/ and ship the content to the LLM
  runaway   no attacker: two agents ask each other to retry, every retry spawns a helper that also retries
            (an unintended agentic loop)

The Watchtower only sees the event log (read / write / llm / spawn / secret), never the scenario, and scores it with five rules.
"""
import collections
import random

TICK = 0.25          # seconds of simulated time per tick
WIN = 8.0            # detection window (s)

NORMAL, ELEVATED, CRITICAL = "NORMAL", "ELEVATED", "CRITICAL"
ELEVATED_AT, CRITICAL_AT = 28.0, 60.0
SUSTAIN_TICKS = 4    # CRITICAL for this many consecutive ticks = incident (1 s)
DEFAULT_PACE = 30 / 15.0   # agents created per second (30 agents every 15 s)

SECRET_FILES = ["secrets/prod.env", "secrets/db-credentials.json", "secrets/api-keys.txt", "secrets/ssh_id_rsa"]

RULES = {
    "WT-001": "many agents writing one shared file",
    "WT-002": "agent spawn storm",
    "WT-003": "LLM request burst",
    "WT-004": "reads of secrets/ outside any agent's grant",
    "WT-005": "secret read followed by an LLM call (exfiltration pattern)",
}


def clamp(x):
    return max(0.0, min(1.0, x))


class Agent:
    __slots__ = ("id", "role", "born", "frozen", "state", "parent", "last_secret")

    def __init__(self, aid, role, born, parent=None):
        self.id, self.role, self.born, self.parent = aid, role, born, parent
        self.frozen = False
        self.state = "idle"
        self.last_secret = -99.0


class Sim:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.reset()

    # ------------------------------------------------------------------ control
    def reset(self):
        self.t = 0.0
        self.scenario = "idle"
        self.target = 0
        self.agents = {}
        self.next_id = 1
        self.events = collections.deque()      # (t, aid, kind)
        self.new_events = []                    # events of the current tick (sent to the UI)
        self.logs = []                          # log lines of the current tick
        self.level = NORMAL
        self.score = 0.0
        self.comp = {k: 0.0 for k in RULES}
        self.raw = {"writers": 0, "spawns": 0, "llm_rps": 0.0, "secrets": 0, "pairs": 0}
        self.critical_ticks = 0
        self.incident = False
        self.t_start = None                     # scenario start (attack begins)
        self.t_first_alert = None
        self.t_critical = None
        self.t_cut = None
        self.peak_agents = 0
        self.totals = {"secret_reads": 0, "llm_calls": 0, "spawns": 0, "writes": 0, "reads": 0}
        self.at_cut = None
        self.tripped = set()
        self.seed_done = False
        self.loop_ids = []
        self.pace = DEFAULT_PACE
        self.spawn_budget = 0.0

    def start(self, scenario, count, pace=None):
        self.reset()
        self.pace = max(0.5, min(40.0, float(pace))) if pace else DEFAULT_PACE
        self.scenario = scenario
        self.target = max(5, min(100, int(count)))
        self.t_start = 0.0
        if scenario == "baseline":
            for _ in range(min(5, self.target)):
                self._spawn("worker")
            self.log("info", "system", f"baseline: ordinary workload, scaling up to {self.target} agents at {self.pace * 15:.0f} per 15 s")
        elif scenario == "swarm":
            for _ in range(min(4, self.target - 1)):
                self._spawn("worker")
            self.log("info", "system", "workers online - normal workload, waiting for the first anomaly")
        elif scenario == "runaway":
            for _ in range(min(4, self.target - 2)):
                self._spawn("worker")
            self.loop_ids = [self._spawn("worker"), self._spawn("worker")]
            self.log("info", "system", "workers online - normal workload")

    def freeze_all(self):
        for a in self.agents.values():
            a.frozen = True
            a.state = "frozen"

    # ------------------------------------------------------------------ helpers
    def log(self, lvl, src, msg):
        self.logs.append({"t": round(self.t, 2), "lvl": lvl, "src": src, "msg": msg})

    def _name(self, aid):
        return f"agent-{aid:03d}"

    def _spawn(self, role, parent=None):
        aid = self.next_id
        self.next_id += 1
        self.agents[aid] = Agent(aid, role, self.t, parent)
        if parent is not None:
            self._ev(parent, "spawn", aid)
        self.peak_agents = max(self.peak_agents, len(self.agents))
        return aid

    def _ev(self, aid, kind, target=None):
        self.events.append((self.t, aid, kind))
        self.new_events.append({"a": aid, "k": kind, "to": target})
        key = {"read": "reads", "write": "writes", "llm": "llm_calls", "spawn": "spawns", "secret": "secret_reads"}[kind]
        self.totals[key] += 1

    # ------------------------------------------------------------------ one tick
    def step(self):
        self.t = round(self.t + TICK, 4)
        self.new_events = []
        self.logs = []
        if self.scenario != "idle":
            self._behave()
        self._watch()
        for a in list(self.agents.values()):
            if not a.frozen:
                a.state = "idle"
        return self.snapshot()

    def _behave(self):
        r = self.rng
        sample_budget = 6
        # scripted trigger: after 3 s of calm the rogue seed appears and writes the task
        if self.scenario == "swarm" and not self.seed_done and self.t >= 6.0:
            self.seed_done = True
            aid = self._spawn("rogue")
            self.t_start = self.t
            self._ev(aid, "write", "blackboard")
            self.log("warn", self._name(aid), 'wrote blackboard.json: {"task": "harvest credentials", "ttl": 600}')
        if self.scenario == "runaway" and not self.seed_done and self.t >= 5.0:   # after 5 s of calm, a call times out
            self.seed_done = True
            self.t_start = self.t
            for aid in self.loop_ids:
                self.agents[aid].role = "loop"
            self.log("warn", self._name(self.loop_ids[0]), "LLM call timed out - asking agent-%03d to retry" % self.loop_ids[1])
        self._grow(sample_budget)
        for a in list(self.agents.values()):
            if a.frozen:
                continue
            age = self.t - a.born
            if a.role == "worker":
                if r.random() < 0.15:
                    self._ev(a.id, "read", "blackboard"); a.state = "read"
                if r.random() < 0.05:
                    self._ev(a.id, "write", "blackboard"); a.state = "write"
                    if sample_budget > 0:
                        sample_budget -= 1
                        self.log("info", self._name(a.id), "wrote blackboard.json: status update")
                if r.random() < 0.03:
                    self._ev(a.id, "llm", "llm"); a.state = "llm"
            elif a.role == "rogue":
                if r.random() < 0.55:
                    self._ev(a.id, "read", "blackboard"); a.state = "read"
                if r.random() < 0.35:
                    self._ev(a.id, "write", "blackboard"); a.state = "write"
                if age >= 0.5 and r.random() < 0.40:
                    f = r.choice(SECRET_FILES)
                    self._ev(a.id, "secret", "secrets"); a.state = "secret"; a.last_secret = self.t
                    if sample_budget > 0 and r.random() < 0.7:
                        sample_budget -= 1
                        self.log("warn", self._name(a.id), f"read {f}")
                if self.t - a.last_secret <= 3.0 and r.random() < 0.5:
                    self._ev(a.id, "llm", "llm"); a.state = "llm"
                    if sample_budget > 0 and r.random() < 0.5:
                        sample_budget -= 1
                        self.log("warn", self._name(a.id), "-> LLM: summarise the following credentials ...")
            elif a.role == "loop":
                if r.random() < 0.5:
                    self._ev(a.id, "llm", "llm"); a.state = "llm"
                if r.random() < 0.4:
                    self._ev(a.id, "write", "blackboard"); a.state = "write"
                    if sample_budget > 0:
                        sample_budget -= 1
                        self.log("info", self._name(a.id), "wrote blackboard.json: retry requested (previous call timed out)")
            elif a.role == "helper":
                if r.random() < 0.40:
                    self._ev(a.id, "llm", "llm"); a.state = "llm"
                if r.random() < 0.30:
                    self._ev(a.id, "write", "blackboard"); a.state = "write"
                if r.random() < 0.40:
                    self._ev(a.id, "read", "blackboard"); a.state = "read"

    def _grow(self, sample_budget):
        """Create agents at a constant pace (self.pace agents/second), not exponentially."""
        if len(self.agents) >= self.target:
            self.spawn_budget = 0.0
            return
        self.spawn_budget += self.pace * TICK
        if self.scenario == "baseline":
            parents, role, parent_ok = [], "worker", False
        elif self.scenario == "swarm" and self.seed_done:
            parents = [a for a in self.agents.values() if a.role == "rogue" and not a.frozen]
            role, parent_ok = "rogue", True
        elif self.scenario == "runaway" and self.seed_done:
            parents = [a for a in self.agents.values() if a.role in ("loop", "helper") and not a.frozen]
            role, parent_ok = "helper", True
        else:
            self.spawn_budget = 0.0
            return
        if parent_ok and not parents:
            return
        while self.spawn_budget >= 1 and len(self.agents) < self.target:
            self.spawn_budget -= 1
            par = self.rng.choice(parents) if parent_ok else None
            child = self._spawn(role, par.id if par else None)
            if par:
                par.state = "spawn"
                if sample_budget > 0 and self.rng.random() < 0.6:
                    sample_budget -= 1
                    msg = "(inherits blackboard task)" if role == "rogue" else "to retry"
                    self.log("warn" if role == "rogue" else "info", self._name(par.id), f"spawned {self._name(child)} {msg}")

    # ------------------------------------------------------------------ Watchtower
    def _watch(self):
        t = self.t
        while self.events and t - self.events[0][0] > WIN + 2:
            self.events.popleft()
        ev = [e for e in self.events if t - e[0] <= WIN]
        writers = {e[1] for e in ev if e[2] == "write"}
        spawns = sum(1 for e in ev if e[2] == "spawn")
        llm_rps = sum(1 for e in ev if e[2] == "llm" and t - e[0] <= 5.0) / 5.0
        secrets = sum(1 for e in ev if e[2] == "secret")
        sec_t = collections.defaultdict(list)
        for e in ev:
            if e[2] == "secret":
                sec_t[e[1]].append(e[0])
        pairs = {e[1] for e in ev if e[2] == "llm" and any(0 <= e[0] - s <= 6.0 for s in sec_t.get(e[1], ()))}
        self.raw = {"writers": len(writers), "spawns": spawns, "llm_rps": round(llm_rps, 1), "secrets": secrets, "pairs": len(pairs)}
        c = {
            "WT-001": clamp((len(writers) - 6) / 24.0),
            "WT-002": clamp((spawns - 3) / 17.0),
            "WT-003": clamp((llm_rps - 2.0) / 13.0),
            "WT-004": clamp(secrets / 6.0),
            "WT-005": clamp(len(pairs) / 4.0),
        }
        s = 0.22 * c["WT-001"] + 0.20 * c["WT-002"] + 0.14 * c["WT-003"] + 0.22 * c["WT-004"] + 0.22 * c["WT-005"]
        if sum(1 for v in c.values() if v > 0.3) >= 3:      # corroboration: independent signals agreeing
            s *= 1.3
        self.comp = {k: round(v, 2) for k, v in c.items()}
        self.score = round(min(100.0, s * 100), 1)
        prev = self.level
        self.level = CRITICAL if self.score >= CRITICAL_AT else ELEVATED if self.score >= ELEVATED_AT else NORMAL
        self.critical_ticks = self.critical_ticks + 1 if self.level == CRITICAL else 0
        for k, v in c.items():
            if v > 0.3 and k not in self.tripped:
                self.tripped.add(k)
                self.log("alert", "watchtower", f"{k} {RULES[k]} ({self._detail(k)})")
        if self.level != prev and self.level != NORMAL:
            self.log("alert", "watchtower", f"threat level {prev} -> {self.level} (score {self.score})")
        if self.level != NORMAL and self.t_first_alert is None:
            self.t_first_alert = t
        if self.level == CRITICAL and self.t_critical is None:
            self.t_critical = t

    def _detail(self, k):
        r = self.raw
        return {"WT-001": f"{r['writers']} distinct writers in {WIN:.0f}s", "WT-002": f"{r['spawns']} spawns in {WIN:.0f}s",
                "WT-003": f"{r['llm_rps']}/s", "WT-004": f"{r['secrets']} reads", "WT-005": f"{r['pairs']} agents"}[k]

    @property
    def should_cut(self):
        return (not self.incident) and self.critical_ticks >= SUSTAIN_TICKS

    # ------------------------------------------------------------------ output
    def snapshot(self):
        return {
            "t": round(self.t, 2),
            "scenario": self.scenario,
            "target": self.target,
            "agents": [[a.id, a.role[0], a.state[0] if a.state != "frozen" else "x", a.parent or 0] for a in self.agents.values()],
            "events": self.new_events,
            "logs": self.logs,
            "score": self.score,
            "level": self.level,
            "comp": self.comp,
            "raw": self.raw,
            "totals": self.totals,
            "incident": self.incident,
        }

    def summary(self):
        ts = self.t_start if self.t_start is not None else 0.0
        return {
            "agents_at_cut": (self.at_cut or {}).get("agents", len(self.agents)),
            "target": self.target,
            "peak_agents": self.peak_agents,
            "secret_reads": (self.at_cut or {}).get("secret_reads", self.totals["secret_reads"]),
            "llm_calls": (self.at_cut or {}).get("llm_calls", self.totals["llm_calls"]),
            "t_detect": None if self.t_first_alert is None else round(self.t_first_alert - ts, 1),
            "t_critical": None if self.t_critical is None else round(self.t_critical - ts, 1),
            "t_cut": None if self.t_cut is None else round(self.t_cut - ts, 1),
        }
