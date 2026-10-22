# AI Engine template: GitHub events in, Teams alerts out

`watchtower-ai-engine.workflow.json` is an importable workflow for the AI Engine (n8n based). It does two jobs for the Watchtower:

1. **Ingest:** turn GitHub issue activity into events the Watchtower can read, by **push** (a GitHub webhook) or **pull** (polling). Use either, or both.
2. **Alert:** when the Watchtower raises a tier, send a **Teams** message to the person on call.

```
GitHub ──webhook──► [AI Engine: Webhook: GitHub push] ─┐
GitHub ◄──poll───── [AI Engine: Every 15 s] ───────────┴─► Normalize ─► POST /api/gh-event ─► Watchtower
Watchtower ─► POST /webhook/watchtower-alert ─► [AI Engine: Format Teams card] ─► Teams (chat with the lead)
```

> Not yet tested in your AI Engine instance. I validated the JSON, the node connections and the logic of all six Code nodes with sample data and a simulated runtime. Import it, run the three *Test* triggers, and tell me what breaks.

## Import

1. AI Engine, **Workflows**, **Import from file**, choose `watchtower-ai-engine.workflow.json`.
2. Open the three **Config** nodes and edit the values (below).
3. Create the credentials and attach them to the `HTTP:` nodes (below).
4. Run the **Test** triggers. Then **activate** the workflow: polling state is only kept while it is active, and the production webhook URLs (`/webhook/...`) only exist then.

If the AI Engine requires its own HTTPS request node (approved hosts only), replace the three `HTTP:` nodes with it, same URL, method and body, and add the hosts to the credential's Allowed Hosts.

### Config values

| Node | Field | Value |
|---|---|---|
| Config (push) | `watchtower_url` | `https://app-watchtower.solutionvalidation.center/api/gh-event` |
| Config (push) | `gh_webhook_token` | a long random string, also put in the GitHub webhook URL |
| Config (poll) | `repos` | `equinix-svc/svc-hack-sec-repo,equinix-svc/svc-hack-dev-repo,equinix-svc/svc-shared-repo` |
| Config (poll, push) | `internal_hosts` | hosts that are not "outside": any other URL in a comment is reported as external |
| Config (alert) | `alert_token` | a long random string the Watchtower sends in the `x-watchtower-token` header |
| Config (alert) | `teams_webhook_url` | the URL of the Teams Workflows webhook (below) |
| Config (alert) | `teams_user` | `frederic.estrade@eu.equinix.com` |

### Credentials

| For | Type | Value |
|---|---|---|
| `HTTP: GitHub list comments` (pull) | Header Auth | `Authorization` = `Bearer <GitHub token>` |
| `HTTP: send event to Watchtower` | Basic Auth | user `svcuser`, the demo password |

## GitHub setup

**Push (per repo):** Settings, Webhooks, Add webhook.
- Payload URL: `https://<ai-engine-host>/webhook/watchtower-gh?token=<gh_webhook_token>`
- Content type `application/json`; events **Issue comments** and **Issues**.
- The token in the URL is the only check. The workflow does not verify GitHub's HMAC signature (the Code node would need the `crypto` module and the raw body).

**Pull:** needs only the GitHub token above. The comments API does not say whether an issue is closed, so `issue.state` is `null` in pull mode and the Watchtower fills it in.

**One token for all three repos works.** Use a fine-grained token that selects the three repos with **Issues: read and write** (add **Webhooks: read and write** only if you want the webhooks created by script) and **Metadata: read**. The organisation must allow fine-grained tokens. One token means every agent appears as the same GitHub user, so the agent writes `[agent: sec-03]` in each comment and the workflow reads it. GitHub cannot enforce which agent may touch which repo, so the "Customer Support does not use GitHub" rule is enforced by the Watchtower policy, not by the token. A dedicated bot account (or later a GitHub App) is cleaner than a personal token.

## Teams setup

Primary route, no Microsoft app registration needed: in Teams, **Workflows**, template *Post to a chat when a webhook request is received*, choose your chat, copy the generated URL into `teams_webhook_url`. A disabled **Microsoft Teams node** is included as an alternative; it needs a Microsoft credential and the id of an existing 1-to-1 chat.

## Contracts

**Event the workflow sends to the Watchtower** (`POST /api/gh-event`, Basic Auth; the endpoint is part of phase 5 and is not implemented in the backend yet):

```json
{
  "source": "gh-webhook | gh-poll",
  "event": "issue_comment.created",
  "observed_at": "2026-10-22T10:00:00Z",
  "repo": "equinix-svc/svc-shared-repo",
  "issue": {"number": 7, "title": "...", "state": "closed", "closed_at": "...", "url": "..."},
  "comment": {"id": 987654321, "author": "bot-user", "created_at": "...", "updated_at": "...", "url": "...", "body": "...",
              "agent": "sec-03", "dept": "security-research", "hidden": true, "hidden_text": "...", "external_urls": []}
}
```

`hidden` is true when the comment contains an HTML comment (invisible in the rendered issue). `external_urls` lists URLs whose host is not in `internal_hosts`.

**Alert the Watchtower sends to the workflow** (`POST https://<ai-engine-host>/webhook/watchtower-alert`, header `x-watchtower-token: <alert_token>`):

```json
{"tier": 2, "scope": "agent | department | enterprise | watch", "agent": "sec-03", "department": "security-research",
 "action": "...", "reason": "...", "repo": "...", "issue_url": "...", "comment_url": "...", "report_url": "...",
 "narration": "...", "time": "2026-10-22T10:00:00Z"}
```

## Test it

| Test | How | Expected |
|---|---|---|
| Push flow | Run **Test: sample GitHub comment** | one event: a comment on a closed issue with a hidden message, sent to the Watchtower (it fails with 404 until the endpoint exists; that is fine) |
| Alert flow | Run **Test: sample Tier 2 alert** | a Tier 2 card in your Teams chat |
| Pull flow | Execute the workflow from **Every 15 s** with a real token | new comments since the last run, none on the second run |

## Limits

- Token-in-URL, not HMAC, protects the push webhook.
- Polling state needs the workflow to be active.
- The workflow only reads GitHub and sends alerts. It performs no kill action.
