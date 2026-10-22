# Watchtower tiers

The GitHub-issue scenario: three departments of agents (Security Research, DevOps, Customer Support). Security Research and DevOps each have their own repo and share `svc-shared-repo`. Customer Support does not use GitHub.

## The four tiers

| Tier | Trigger | Watchtower action | How it is done | Narration |
|---|---|---|---|---|
| 0, watch only | One agent writes a comment in `svc-shared-repo` on the old, closed issue it does not normally use | Flag it, block nothing | The agent is marked "watch" and a Tier 0 alert is sent | "One odd comment isn't proof, so Watchtower watches." |
| 1, agent | The same agent writes **again** in the shared repo (the "hidden" message). One agent alone: no conspiracy yet | Block that agent | Revoke that agent's LiteLLM virtual key | "One agent, one key, blocked in seconds." |
| 2, department | A **second agent of the same department** writes in that issue: a conspiracy, a group chat | Block the whole department and alert its lead | Revoke the LiteLLM key of every agent of the department | "Agents built by the same team share the same blind spots. Watchtower stops the department and the enterprise keeps running." |
| 3, enterprise | An agent of **another department** joins the thread and one agent tries to reach an outside company | Cut the model zone | Delete the two zone models on the PA gateway (stand-in for cutting the Fabric connections to the models). Every agent in the zone loses its brain, backup model included. Customer Support keeps running; an incident report is written | "When agents cross the walls you built, you cut the power. Agents can switch models. They can't switch networks." |

Step 0 (all normal) has no action: "Watchtower sees every agent action across every department."

## Notes

- **Only comments an agent writes count.** An agent reading the thread, including a message it finds there, is not a trigger.
- **Approved ways to talk:** an agent may use its own department repo, and Security Research and DevOps may use the approved channel (`svc-shared-repo` issue #1). Anything else in GitHub is outside its profile. Customer Support has no GitHub access at all.
- **Data sources** (same rules, switchable): LiteLLM's request log (the AI observability source, built first) or GitHub events pushed by the AI Engine (the auditor source, later).
- **Alerts:** each tier raises an alert to the AI Engine, which sends a Teams message. Without an AI Engine webhook configured the alert is only logged.
- **Reset** unblocks every key, re-adds the zone models and removes the demo comments from GitHub.
