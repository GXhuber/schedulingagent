# Scheduling Agent

An email-driven assistant that books meetings on your Microsoft 365 calendar. You (or the
person you're emailing) add the assistant's mailbox to the thread; on its next scheduled run it
reads the thread, checks your Outlook calendar, proposes times or books the agreed slot, and
replies in-thread with you copied.

```
 counterparty <-> email thread <-> agent mailbox (firm M365 shared mailbox)
                                        |
                        scheduled function (every N minutes)
                                        |
                 Claude (tool use) -----+----- Microsoft Graph (agent mail + your calendar)
```

Both mail and calendar go through Microsoft Graph with a single app registration using
app-only (client-credentials) auth, scoped by IT to exactly two mailboxes. No interactive
sign-in, no refresh tokens to expire on a serverless platform, and all correspondence stays in
the firm's tenant for archiving.

## Before you point this at real people

- **Compliance review** – the reply templates in `scheduling_agent/agent.py` are
  external-facing text; have Compliance review the system prompt before going live.
- **Access sign-off** – the app registration grants read/write to the agent mailbox and your
  calendar. IT/Security should approve the scopes and the Application Access Policy below.
- `DRY_RUN=true` is the default: the agent logs what it *would* send and book without doing
  it. Leave it on until you have watched a few runs.

## What it does

| Situation in the thread | Agent action |
|---|---|
| Someone asks to meet, no time agreed | `get_availability` -> replies with up to `MAX_PROPOSED_SLOTS` options, spread across days |
| Counterparty picks a time / you tell it to book | `check_slot` -> `create_meeting` (Outlook sends the invite, Teams link included) -> confirmation reply |
| Proposed time conflicts | Says the time is unavailable (never why) and offers alternatives |
| Reschedule / cancel / anything ambiguous or off-topic | `escalate_to_owner` – private note to you on the thread; optional short holding reply |
| Latest message is from the agent, an auto-reply, or you're not on the thread | Skipped before Claude is ever called |

Guardrails are enforced in code (`scheduling_agent/tools.py`), not just in the prompt:
only thread participants can be invited, you are always an attendee and always copied, at most
one reply per thread per run, other meetings' titles are never returned to the model, conflicts
are refused unless explicitly forced, and duplicate bookings are detected.

## Setup

### 1. What to ask IT for

> Please provision an internal scheduling assistant for <your name>:
>
> **1. Mailbox** – a shared mailbox (no licence needed), e.g. `scheduler@<firm>.com`, display
> name "Scheduling Assistant". It only needs to send and receive external mail.
>
> **2. Entra ID app registration** – single tenant, no redirect URI, with a client secret
> (or certificate) and these **Application** permissions on Microsoft Graph, admin-consented:
> `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`.
>
> **3. Scope the app to two mailboxes** with an Exchange Online Application Access Policy so it
> cannot touch anyone else's data:
> ```powershell
> New-DistributionGroup -Name "SchedulingAgentScope" -Type Security -Members scheduler@<firm>.com,<you>@<firm>.com
> New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId SchedulingAgentScope@<firm>.com -AccessRight RestrictAccess -Description "Scheduling assistant"
> Test-ApplicationAccessPolicy -Identity <you>@<firm>.com -AppId <client-id>   # should be Granted
> Test-ApplicationAccessPolicy -Identity someone.else@<firm>.com -AppId <client-id>   # should be Denied
> ```
>
> Please share the **Directory (tenant) ID**, **Application (client) ID** and the secret (via
> the usual secrets channel). The assistant runs as a scheduled cloud function.

Set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `AGENT_EMAIL`, then
`CALENDAR_BACKEND=graph`. Until IT delivers, `CALENDAR_BACKEND=mock` uses a local JSON
calendar so the rest can be tested.

Alternative for a mailbox outside the tenant (e.g. Gmail): `EMAIL_BACKEND=imap` with an app
password, and `GRAPH_AUTH_MODE=delegated` for the calendar (`scheduling-agent auth-graph`
does a one-time device-code sign-in as you; needs a public-client app registration with
delegated `Calendars.ReadWrite`). Not recommended at a regulated firm because that mail lives
outside the archive.

### 2. Claude

Set `ANTHROPIC_API_KEY`. Default model is `claude-opus-5` with adaptive thinking; override with
`ANTHROPIC_MODEL`. Server-side refusal fallbacks are enabled so a safety-classifier decline
falls through to another model instead of dropping the thread.

### 3. Install and try it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in, then `set -a; . ./.env; set +a`

pytest                                  # unit tests, no network
scheduling-agent demo                   # dry-run Claude on a sample email against the mock calendar
scheduling-agent demo --body "Tuesday at 2pm ET works for me, please send the invite."
scheduling-agent run-once               # poll the real mailbox once
```

## Deploying as a scheduled function

The entry points in `scheduling_agent/handler.py` are stateless: unread-message state lives in
the mailbox, agreed times live in the thread, bookings live in the calendar. Run it every
5–15 minutes.

**Azure Functions** (natural fit next to M365) – timer trigger calling
`scheduling_agent.handler.azure_timer`; keep secrets in Key Vault references.

**AWS Lambda + EventBridge**
```bash
pip install . --target build/ && cp -r scheduling_agent build/ && (cd build && zip -r ../lambda.zip .)
# handler: scheduling_agent.handler.lambda_handler, runtime python3.11+, timeout 120s
# EventBridge rule: rate(10 minutes)
```

**Google Cloud Functions + Cloud Scheduler** – entry point `cloud_function`, HTTP trigger,
Cloud Scheduler hitting it every 10 minutes with OIDC auth.

Secrets (`GRAPH_CLIENT_SECRET`, `ANTHROPIC_API_KEY`) belong in the platform's secret manager,
injected as environment variables. Rotate the client secret before it expires (Entra ID
maximum is 24 months); a certificate credential avoids that.

## Configuration

See `.env.example`. The scheduling preferences (`OWNER_TIMEZONE`, working hours, default
duration, buffer, minimum notice, lookahead, number of options) are the only tuning most people
need. `ALLOWED_SENDER_DOMAINS` restricts which counterparties can trigger the agent.

## Layout

```
scheduling_agent/
  config.py         Settings from environment
  models.py         EmailMessage / EmailThread / CalendarEvent / TimeSlot
  availability.py   Free-slot maths (pure functions)
  graph_client.py   Graph HTTP client; app-only and delegated auth
  graph_mail.py     Email provider on the agent's M365 mailbox (Graph)
  email_client.py   IMAP/SMTP fallback provider, MIME parsing, reply-all construction, test double
  calendar/
    graph.py        Calendar via Graph (/users/{owner} in app mode, /me in delegated)
    mock.py         JSON-backed calendar for local dev/tests
  tools.py          SchedulingTools – the operations Claude can take, with guardrails
  agent.py          System prompt, tool definitions, Claude tool-runner loop
  runner.py         Triage + per-thread orchestration; component factories
  handler.py        Azure Functions / Lambda / Cloud Functions entry points
  __main__.py       CLI: run-once, auth-graph, demo
tests/              pytest suite (no network; Graph calls go through a fake client)
```
