# YTPipe

Never miss an upload from the channels you actually care about — with a
Spanish summary waiting for you.

YTPipe is a personal YouTube monitoring backend. It watches your explicitly
chosen channels, detects new uploads via the YouTube Data API, fetches
transcripts, summarizes them with a local LLM, and notifies you over Telegram
and email — plus a mobile API for a companion app. Send it any video URL over
Telegram and it summarizes that too, on demand.

It is a single-user system: one FastAPI service, one PostgreSQL database, no
UI, no background-job infrastructure. Everything runs unattended once
configured.

## Why it exists

YouTube notifications are unreliable, and keeping up with many channels means
either watching everything or missing the good parts. YTPipe fixes both sides:

- **Exactly one notification per new video.** Uploads-playlist detection,
  database-level dedup constraints, and idempotent writes make duplicates
  structurally impossible.
- **Summaries, not just links.** Every detected video goes through a
  transcript → summary pipeline on your own hardware, so you can decide in
  seconds whether a 40-minute video deserves your evening.
- **You choose what is watched.** Imported subscriptions are *not* monitored
  by default — you enable monitoring per channel, and the first poll only
  establishes a silent baseline. No notification spam, ever.
- **Quota-safe by design.** Sequential polling with a configurable daily
  quota budget and a safety stop before YouTube's real limit. One channel
  failing never aborts the run.
- **Private by construction.** Single user, loopback-first config, separate
  bearer tokens for admin vs. mobile surfaces, protected API docs, and
  Telegram bot tokens that never pass through HTTP logs.

## How it works

```text
YouTube Data API (uploads playlists)
        │  poll monitored channels only, sequentially
        ▼
detection → baseline or new video → transcript → summary → notify
                                              local LLM    Telegram / email /
                                              (llama.cpp)  mobile push
```

1. **Subscribe & select.** Google OAuth (read-only scope) imports your
   subscriptions into a catalog. You enable monitoring per channel via the
   internal API.
2. **Poll.** A protected internal endpoint runs one cycle: for each monitored
   channel it reads the uploads playlist, compares with stored sync state,
   and records new videos. Runs report `success` / `partial` / `failed` with
   per-channel errors instead of aborting.
3. **Pipeline.** Each new video flows through durable stages —
   `transcript` → `summary` → `telegram` — with per-stage retry, skip, and
   failure states. If a stage keeps failing, you get an explicit fallback
   message stating the reason instead of silence.
4. **Summary.** A small local instruction model served by llama.cpp writes a
   Spanish summary (`RESUMEN` → `PUNTOS CLAVE` → `CONCLUSIÓN`, evidence-based,
   no filler). Long transcripts are chunked and re-synthesized.
5. **On demand.** Message `/summary <youtube-url>` to the Telegram bot and a
   long-polling listener queues the request in PostgreSQL; the same pipeline
   processes it and replies in your private chat. No webhook, no public
   inbound port.

Summarization routing is broker-capable but conservative: the direct
llama.cpp route is the operational default, and any broker cutover happens
only through an explicit, reconciled canary with `direct` retained as the
rollback route.

## Features

- **Polling core:** uploads-playlist detection (quota-efficient, never
  search-based), monitored-only channels, silent baselines, sequential
  processing, quota budget + safety stop, reconciliation passes, execution
  lock so two polls never overlap.
- **Content pipeline:** transcript fetch, Spanish LLM summaries with a fixed
  output contract (`specs/summary_output_format.spec.md`), Shorts toggle,
  startup catch-up batching, per-video recovery, crash-safe submission
  handling.
- **Telegram:** outbound notifications with failure notices, plus inbound
  `/summary` commands via outbound-only long polling and a durable command
  queue (exactly one listener; strict local URL parsing — supplied URLs are
  never fetched and redirects never followed).
- **Email:** Resend delivery with `fake` / `resend` / `disabled` modes and
  one next-cycle retry for transient failures only.
- **Mobile backend:** separate mobile bearer token, UI-friendly channel
  catalog, read-only activity/history, Expo push registration and test sends
  (`specs/mobile_*.spec.md`, `specs/implementation/11*.spec.md`). Poll-step
  push fan-out is an intentionally deferred phase.
- **Observability:** `/status` surfaces sync, polling, delivery, quota, and
  channel state; startup and poll runs log processed/succeeded/failed/skipped
  counts.

## Quick start

Requirements: Python >= 3.13, PostgreSQL, and Google OAuth credentials with
YouTube Data API access.

```bash
git clone <your-copy-of-the-repo> && cd YTPipe
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then fill in DATABASE_URL + Google credentials
alembic upgrade head          # create the schema
uvicorn app.main:app --reload
curl -s localhost:8000/health
```

Then, in order:

1. **Authenticate:** open `GET /auth/google`, approve the read-only scopes,
   and let `GET /auth/callback` persist the tokens (auto-refresh included;
   manual re-auth only if the refresh token dies).
2. **Import & select:** sync subscriptions, list the catalog, and enable
   monitoring per channel (imported channels start unmonitored; the first
   poll of a newly enabled channel sets a silent baseline).
3. **Poll:** trigger the protected internal poll endpoint with the admin
   bearer token; inspect the aggregate outcome
   (`channels_processed`, `new_videos_detected`, `quota_blocked`) and
   `/status`.
4. **Notify:** enable Telegram and/or email in `.env` to receive summaries;
   message `/summary <youtube-url>` to the bot for on-demand summaries.

Full test suite: `pytest`. API docs (Swagger/ReDoc) are served at `/docs`
and `/redoc` — open in `local`, admin-bearer-protected everywhere else.

## Configuration

Everything is environment-driven (`app/core/settings.py`); `.env.example`
documents every knob. The safe defaults run fully offline-ish: fake email,
disabled Telegram/push/broker probes, local Postgres.

| Area | Key variables |
|---|---|
| App & DB | `APP_ENV` (`local`/`staging`/`production`), `APP_HOST`, `APP_PORT`, `DATABASE_URL` (`postgresql+psycopg://…`, SSL required outside `local`) |
| Auth | `INTERNAL_API_BEARER_TOKEN`, `MOBILE_API_BEARER_TOKEN`, `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` |
| Polling | `POLL_INTERVAL_MINUTES`, `POLL_QUOTA_DAILY_BUDGET`, `POLL_QUOTA_SAFETY_STOP_ENABLED`, `SHORTS_PROCESSING_ENABLED` |
| Summaries | `LLAMA_CPP_BASE_URL/TIMEOUT/MAX_TOKENS`, `SUMMARY_ROUTE` (empty = direct; the only rollback route) |
| Telegram | `TELEGRAM_NOTIFICATIONS_ENABLED`, `TELEGRAM_BOT_TOKEN/CHAT_ID`, `TELEGRAM_COMMANDS_ENABLED/ALLOWED_USER_ID/BOT_USERNAME` |
| Email | `EMAIL_DELIVERY_MODE` (`fake`/`resend`/`disabled`), `RESEND_API_KEY/FROM_EMAIL` |
| Mobile push | `PUSH_NOTIFICATIONS_ENABLED`, `EXPO_PUSH_*` |
| Broker (rollout) | `BROKER_BASE_URL/BEARER_TOKEN/TIMEOUT_SECONDS`, `*_PROBE*_ENABLED` (probes only; never the normal runtime) |

Non-`local` environments fail fast on placeholder secrets, localhost
database URLs, non-HTTPS OAuth redirects, and misconfigured Telegram/email
settings — see `validate_runtime_config()`.

## API surface

- **Public:** `GET /health`, Google OAuth pair (`/auth/google`,
  `/auth/callback`), `/` service banner.
- **Admin (bearer):** channel catalog + per-channel monitoring switches,
  protected polling trigger and reconciliation, Telegram command intake,
  `/status`, `/openapi.json` + `/docs` + `/redoc`.
- **Mobile (separate bearer):** channel catalog views, activity/history,
  push registration, preferences, and test sends.

Delivery semantics are explicit: channel failures are recorded per channel,
email uses `pending_retry` → one next-cycle retry → `failed`, and pipeline
stages carry explicit terminal states instead of silent drops.

## Stack and layout

- **Python 3.13**, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL via psycopg,
  `google-api-python-client` + `google-auth-oauthlib`,
  `youtube-transcript-api`, httpx, uvicorn, pytest.
- **No** Celery, Redis, UI, or multi-user support — PostgreSQL is the only
  queue (polling cycles, pipeline stages, Telegram command queue).

```text
app/
├── main.py            # create_app(): routers, CORS, protected docs, lifespan
├── api/routes/        # auth, channels, subscriptions, polling, status,
│                      # activity, mobile_push, telegram_commands, health
├── core/              # Settings + validation, security helpers
├── db/                # session/engine
├── models/            # User, OAuthAccount, Channel, Video, SyncState,
│                      # NotificationDelivery, PipelineStage, push + commands
├── services/          # polling, pipeline, transcript, summarization,
│                      # telegram, email, mobile_push, broker_*, llama_recovery
├── contracts/         # mobile API + compat boundaries
└── cli/               # operational CLIs (incl. manual broker probes)
alembic/versions/      # 12 migrations to date (schema is append-only in spirit)
specs/                 # product + architecture source of truth, phase index
systemd/               # homelab units: poll monitor, telegram listener,
│                      # llama monitor/restart, postgres (+ operator README)
scripts/               # host monitors + telegram command listener entrypoint
tests/                 # pytest suite (unit + contract + recovery)
```

Product, architecture, polling, retry, and guardrail decisions live in
`specs/`; the build order in
`specs/implementation/00_implementation_phase_index.spec.md` (MVP phases
01–06, mobile 08–11, Telegram commands 12A–12C, broker path Y00–Y02d).
Per-repo workflow rules are in `AGENTS.md`.

## Operations

- Homelab systemd units for the API, poll monitor, Telegram command
  listener, llama.cpp monitor (with optional narrowly-scoped auto-restart via
  sudoers), and Postgres — see `systemd/README.md`. Units are never
  installed automatically; bearer/Telegram secrets stay in `.env`, never in
  unit files.
- Single execution lock guards polling and pipeline startup batches against
  overlap; Telegram long polling allows exactly one consumer per bot.
- Broker changes are gated: connectivity acceptance passed on a disposable
  stack; any production canary needs separate operational approval and full
  broker/YTPipe/Telegram reconciliation, with `direct` as the explicit
  rollback.

## Explicitly out of scope

UI, multi-user support, extra notification channels beyond the approved set,
Celery/Redis, webhook-based Telegram intake, fetching arbitrary supplied
URLs, and authenticated scraping of any kind. If a phase reveals a spec gap,
the specs change first — implementation follows.
