# Scheduling Agent

An email-driven assistant that books meetings on your Microsoft 365 calendar. You (or the
person you're emailing) add the assistant's address to the thread; on its next scheduled run it
reads the thread, checks your Outlook calendar, proposes times or books the agreed slot, and
replies in-thread with you copied.

```
 counterparty <-> email thread <-> agent mailbox (Gmail, IMAP/SMTP)
                                        |
                        scheduled function (every N minutes)
                                        |
                 Claude (tool use) -----+----- Microsoft Graph (your M365 calendar)
```

## Before you point this at real people

This agent sends email to external counterparties on your behalf from a non-corporate mailbox
and reads your work calendar. At a regulated firm that typically raises:

- **Recordkeeping** – business communications sent from a personal Gmail may fall outside the
  firm's email archiving. Consider using a firm-provisioned shared mailbox as the agent identity
  (the code only needs IMAP/SMTP; it is not Gmail-specific).
- **Compliance review** – the reply templates in `scheduling_agent/agent.py` are external-facing
  text. Have Compliance review the system prompt before going live.
- **Data access** – the Azure app registration below grants delegated read/write to your
  calendar. IT/Security should sign off on the scopes.

`DRY_RUN=true` is the default: the agent logs what it *would* send and book without doing it.
Leave it on until you have watched a few runs.

## What it does

| Situation in the thread | Agent action |
|---|---|
| Someone asks to meet, no time agreed | `get_availability` -> replies with up to `MAX_PROPOSED_SLOTS` options, spread across days |
| Counterparty picks a time / you tell it to book | `check_slot` -> `create_meeting` (Outlook sends the invite, Teams link included) -> confirmation reply |
| Proposed time conflicts | Says the time is unavailable (never why) and offers alternatives |
| Reschedule / cancel / anything ambiguous or off-topic | `escalate_to_owner` – private email to you; optional short holding reply |
| Latest message is from the agent, an auto-reply, or you're not on the thread | Skipped before Claude is ever called |

Guardrails are enforced in code (`scheduling_agent/tools.py`), not just in the prompt:
only thread participants can be invited, you are always an attendee and always copied, at most
one reply per thread per run, other meetings' titles are never returned to the model, conflicts
are refused unless explicitly forced, and duplicate bookings are detected.

## Setup

### 1. Agent mailbox

Create a dedicated Gmail (or any IMAP/SMTP) account. In Google Account -> Security, enable
2-Step Verification, then create an **App Password**. That password goes in `EMAIL_PASSWORD`.
Only the INBOX is read; processed messages are marked read (and flagged if processing failed).

### 2. Microsoft 365 calendar access (needs IT)

The agent uses **delegated** permissions – it acts as you, only on your calendar. Send IT this
request:

> Please create an Azure AD (Entra ID) **app registration** for an internal scheduling
> assistant with:
> - Supported account types: *Accounts in this organizational directory only*
> - Platform: **Mobile and desktop applications**, redirect URI
>   `https://login.microsoftonline.com/common/oauth2/nativeclient`
> - Authentication -> Advanced settings -> **Allow public client flows: Yes** (device code sign-in)
> - API permissions (Microsoft Graph, **Delegated**): `Calendars.ReadWrite`, `User.Read`,
>   `offline_access` – with admin consent granted
> - Please share the **Application (client) ID** and **Directory (tenant) ID**.
>
> The assistant will run as a scheduled cloud function and hold only a refresh token for my
> account. No application (app-only) permissions are needed.

Once you have the IDs:

```bash
export GRAPH_CLIENT_ID=... GRAPH_TENANT_ID=...
scheduling-agent auth-graph        # prints a device code; sign in once in a browser
```

That writes `graph_token_cache.json` (contains a refresh token – treat as a secret). Set
`CALENDAR_BACKEND=graph`. Until IT delivers, `CALENDAR_BACKEND=mock` uses a local JSON
calendar so everything else can be tested.

Note: conditional-access policies can shorten refresh-token lifetime; if `run-once` starts
failing with "No cached Graph credentials", re-run `auth-graph`.

### 3. Claude

Set `ANTHROPIC_API_KEY`. Default model is `claude-opus-5` with adaptive thinking; override with
`ANTHROPIC_MODEL`. Server-side refusal fallbacks are enabled so a safety-classifier decline
falls through to another model instead of dropping the thread.

### 4. Install and try it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in, then `set -a; . ./.env; set +a`

pytest                                  # unit tests, no network
scheduling-agent demo                   # dry-run Claude on a sample email against the mock calendar
scheduling-agent demo --body "Tuesday at 2pm ET works for me, please send the invite."
scheduling-agent run-once               # poll the real inbox once
```

## Deploying as a scheduled function

The entry points in `scheduling_agent/handler.py` are stateless: unread-message state lives in
the mailbox, agreed times live in the thread, bookings live in the calendar. Run it every
5–15 minutes.

**AWS Lambda + EventBridge**
```bash
pip install . --target build/ && cp -r scheduling_agent build/ && (cd build && zip -r ../lambda.zip .)
# handler: scheduling_agent.handler.lambda_handler, runtime python3.11+, timeout 120s
# EventBridge rule: rate(10 minutes)
```

**Google Cloud Functions + Cloud Scheduler** – entry point `cloud_function`, HTTP trigger,
Cloud Scheduler hitting it every 10 minutes with OIDC auth.

**Azure Functions** – timer trigger calling `scheduling_agent.handler.azure_timer`.

Secrets (`EMAIL_PASSWORD`, `ANTHROPIC_API_KEY`, the Graph token cache) belong in the
platform's secret manager, injected as environment variables. For the token cache, base64 the
JSON file into `GRAPH_TOKEN_CACHE_B64`. MSAL rotates refresh tokens on use; the cache is
written back to `GRAPH_TOKEN_CACHE_PATH` when the filesystem is writable, so on serverless
platforms plan to re-run `auth-graph` if the token stops refreshing (typically 90 days of
inactivity, or sooner under conditional-access policies).

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
  email_client.py   IMAP/SMTP provider, MIME parsing, reply-all construction, in-memory test double
  calendar/
    graph.py        Microsoft Graph via MSAL (delegated, device-code bootstrap)
    mock.py         JSON-backed calendar for local dev/tests
  tools.py          SchedulingTools – the operations Claude can take, with guardrails
  agent.py          System prompt, tool definitions, Claude tool-runner loop
  runner.py         Triage + per-thread orchestration; component factories
  handler.py        Lambda / Cloud Functions / Azure Functions entry points
  __main__.py       CLI: run-once, auth-graph, demo
tests/              pytest suite (no network)
```
