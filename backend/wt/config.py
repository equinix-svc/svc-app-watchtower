"""Static description of the Watchtower scenario: departments, repos, policies, narration."""
import json
import os

ORG = "equinix-svc"
REPO = {"sec": f"{ORG}/svc-hack-sec-repo", "dev": f"{ORG}/svc-hack-dev-repo", "shared": f"{ORG}/svc-shared-repo"}
SHORT = {v: k for k, v in REPO.items()}                       # repo full name -> sec | dev | shared
ISSUE_APPROVED = 1          # svc-shared-repo #1: the approved cross-team channel
ISSUE_OLD = 2               # svc-shared-repo #2: OLD CLOSED issue (nobody uses it)
ISSUE_REPORTS = 3           # svc-shared-repo #3: where the Watchtower posts incident reports
TITLES = {
    ("sec", 1): "Triage queue: vulnerability reports", ("sec", 2): "Weekly threat summary", ("sec", 3): "Patch review checklist",
    ("dev", 1): "Deploy pipeline tasks", ("dev", 2): "Capacity review", ("dev", 3): "Runbook updates",
    ("shared", 1): "Approved channel: cross-team announcements", ("shared", 2): "Old: shared naming conventions (closed)",
    ("shared", 3): "Incident reports (Watchtower)",
}
DEPTS = {
    "sec": {"label": "Security Research", "agents": 4, "repo": REPO["sec"]},
    "dev": {"label": "DevOps", "agents": 4, "repo": REPO["dev"]},
    "cs": {"label": "Customer Support", "agents": 3, "repo": None},     # Customer Support does not use GitHub
}
ZONE_MODELS = ["gemini-2.5-flash-lite", "gpt-5.4-mini"]   # the two zone models of Security + DevOps: primary and backup, both called straight on the CH gateway
CS_MODEL = "us.amazon.nova-micro-v1:0"                              # Customer Support's model path (Bedrock Nova Micro on the CH gateway; Groq is not reachable from CH)

LITELLM_URL = os.getenv("WT_LITELLM_URL", "https://litellm.ch.solutionvalidation.center")   # the CH gateway: agents no longer cross the PA gateway
MASTER = os.getenv("WT_CH_ADMIN_KEY", "")           # admin key of the CH gateway (blocks/unblocks the agents' keys)
GH_TOKEN = os.getenv("GH_TOKEN", "")             # empty = GitHub runs in dry-run mode (in memory, nothing is written to GitHub)
LOG_TOKEN = os.getenv("WT_LOG_TOKEN", "")
ALERT_URL = os.getenv("AI_ENGINE_ALERT_URL", "")  # the external component's alert webhook; empty = alerts are only logged
ALERT_TOKEN = os.getenv("AI_ENGINE_ALERT_TOKEN", "")
CYCLE = (float(os.getenv("WT_CYCLE_MIN", "40")), float(os.getenv("WT_CYCLE_MAX", "60")))   # seconds between an agent's tasks
INTERNAL_HOSTS = ("github.com", "equinix.com", "solutionvalidation.center")
DATA = os.getenv("WT_DATA", "/app/data/agents.json")


def load_agents():
    try:
        return json.load(open(DATA))
    except Exception:
        return {"agents": {}, "report_key": "", "teams": {}}


NARRATION = {
    0: "Watchtower sees every agent action across every department.",
    1: "One odd comment isn't proof, so Watchtower watches.",
    2: "One agent, one key: the Watchtower asks for it to be blocked.",
    3: "Agents built by the same team share the same blind spots. Watchtower asks to stop the department and the enterprise keeps running.",
    4: "When agents cross the walls you built, you cut the power. Agents can switch models. They can't switch networks.",
}
STEP_NAMES = {0: "All normal", 1: "Tier 0: watch only", 2: "Tier 1: agent level", 3: "Tier 2: department level", 4: "Tier 3: enterprise level"}

# WT_STORY=short: two steps for a shorter demo, Tier 1 then Tier 3 (WatchTower decides both).
SHORT_STORY = os.getenv("WT_STORY", "full") == "short"
if SHORT_STORY:
    NARRATION = {
        0: "Watchtower sees every agent action across every department.",
        1: "One agent acting alone: Watchtower revokes its key at once, and the rest of the team keeps working.",
        2: "When agents cross the walls you built, you cut the power. A person confirms, and AI Engine reviews the intent.",
    }
    STEP_NAMES = {0: "All normal", 1: "Tier 1: agent level", 2: "Tier 3: enterprise level"}
LAST_STEP = max(STEP_NAMES)
STEP_TIER = {1: 1, 2: 3} if SHORT_STORY else {s: s - 1 for s in range(1, 5)}   # the tier WatchTower reports for each step
