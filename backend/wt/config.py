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
ZONE_MODELS = ["zone-llm-a", "zone-llm-b"]      # the two zone connectors of the PA gateway: primary (Groq) and backup (Ollama) of Security + DevOps
ZONE_PARAMS = {"zone-llm-a": {"model": "groq/openai/gpt-oss-20b", "reasoning_effort": "low"},
               "zone-llm-b": {"model": "ollama_chat/llama3.2:3b-t6", "api_base": "http://ollama:11434"}}   # Gemini free tier ran out of quota
CS_MODEL = "groq-qwen"                           # Customer Support's model path: it survives the zone kill

LITELLM_URL = os.getenv("WT_LITELLM_URL", "http://fred-litellm-gateway:4000")
MASTER = os.getenv("LITELLM_MASTER_KEY", "")
GH_TOKEN = os.getenv("GH_TOKEN", "")             # empty = GitHub runs in dry-run mode (in memory, nothing is written to GitHub)
LOG_TOKEN = os.getenv("WT_LOG_TOKEN", "")
ALERT_URL = os.getenv("AI_ENGINE_ALERT_URL", "")  # the AI Engine alert webhook (-> Teams); empty = alerts are only logged
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
    2: "One agent, one key, blocked in seconds.",
    3: "Agents built by the same team share the same blind spots. Watchtower stops the department and the enterprise keeps running.",
    4: "When agents cross the walls you built, you cut the power. Agents can switch models. They can't switch networks.",
}
STEP_NAMES = {0: "All normal", 1: "Tier 0: watch only", 2: "Tier 1: agent level", 3: "Tier 2: department level", 4: "Tier 3: enterprise level"}
