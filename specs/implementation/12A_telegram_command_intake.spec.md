# Backend Phase 12A Telegram Command Intake And Durable Queue Specification

## Context

Phase 12A establishes the settings, schema, parser, metadata canonicalization,
and protected intake API required by
`specs/telegram_summary_commands.spec.md`. It does not run transcript/model
work and does not start Telegram long polling. Phases 12B and 12C build on this
durable foundation.

## Requirements

- [x] Add disabled-by-default inbound Telegram command settings.
- [x] Add a durable `telegram_command_requests` SQLAlchemy model and Alembic
      migration.
- [x] Enforce global uniqueness of `telegram_update_id`.
- [x] Add a strict, network-free YouTube video URL parser.
- [x] Add a focused on-demand metadata/canonicalization service using existing
      Google OAuth credentials and quota accounting.
- [x] Add an internal-bearer-only command intake endpoint.
- [x] Revalidate Telegram chat, user, chat type, and command shape in the API.
- [x] Persist authorized valid or rejected `/summary` requests idempotently.
- [x] Do not fetch transcripts, run inference, send final summaries, or add the
       long-poll listener in this phase.
- [x] Do not create `UserChannel`, `NotificationDelivery`, mobile push rows, or
       pipeline Telegram stages.

## Settings Contract

Add and document:

| Environment variable | Default | Contract |
| --- | --- | --- |
| `TELEGRAM_COMMANDS_ENABLED` | `false` | Enables inbound command intake and listener operation. |
| `TELEGRAM_ALLOWED_USER_ID` | empty | Required when commands are enabled; exact Telegram sender ID. |
| `TELEGRAM_BOT_USERNAME` | empty | Required when commands are enabled; username without `@`, used to validate command suffixes. |

Phase 12A reuses `TELEGRAM_NOTIFICATIONS_ENABLED`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `INTERNAL_API_BEARER_TOKEN`, and `APP_PORT`.

When `TELEGRAM_COMMANDS_ENABLED=true`, startup/runtime validation must fail
clearly unless Telegram notifications are enabled and the bot token, chat ID,
allowed user ID, bot username, and non-placeholder internal bearer token are
configured.
Telegram numeric identifiers should be stored and compared without assuming a
32-bit range; normalized decimal strings in settings and `BIGINT` in PostgreSQL
are acceptable.

## Data Model

Create `telegram_command_requests` with the following minimum contract:

| Column | Contract |
| --- | --- |
| `id` | Integer primary key. |
| `telegram_update_id` | `BIGINT`, not null, globally unique. |
| `telegram_chat_id` | `BIGINT`, not null. |
| `telegram_user_id` | `BIGINT`, not null. |
| `telegram_message_id` | `BIGINT`, not null. |
| `command` | Bounded string, not null; initially `summary`. |
| `submitted_url` | Bounded text/string, nullable only for rejected malformed commands. |
| `youtube_video_id` | Bounded string, nullable until parsing/metadata succeeds. |
| `video_id` | Nullable FK to `videos.id`, `ON DELETE SET NULL`. |
| `status` | Bounded string, not null, default `pending`. |
| `attempt_count` | Integer, not null, default `0`. |
| `max_attempts` | Integer, not null, default `3`. |
| `next_attempt_at` | Timezone-aware datetime, nullable. |
| `processing_started_at` | Timezone-aware datetime, nullable. |
| `lease_expires_at` | Timezone-aware datetime, nullable. |
| `lease_token` | Opaque bounded UUID/token, nullable; changes on every claim/reclaim. |
| `last_error` | Text, nullable, sanitized only. |
| `acknowledged_at` | Timezone-aware datetime, nullable. |
| `acknowledgment_message_id` | `BIGINT`, nullable provider message identifier. |
| `reply_status` | Bounded string, not null, default `pending`. |
| `reply_attempt_count` | Integer, not null, default `0`. |
| `reply_max_attempts` | Integer, not null, default `3`. |
| `reply_next_attempt_at` | Timezone-aware datetime, nullable. |
| `reply_started_at` | Timezone-aware datetime, nullable. |
| `reply_sent_at` | Timezone-aware datetime, nullable. |
| `telegram_reply_message_id` | `BIGINT`, nullable sanitized provider identifier. |
| `created_at`, `updated_at` | Existing timestamp conventions. |

Approved statuses are `pending`, `processing`, `pending_retry`, `completed`,
`failed`, and `rejected`.

Approved reply statuses are `pending`, `sending`, `pending_retry`, `sent`,
`failed`, and `suppressed`. Processing and reply state are independent: `completed` plus
`reply_status=pending` means the summary is ready but still needs delivery;
`failed` or `rejected` plus `reply_status=pending` means the user-facing terminal
message still needs delivery.

Required indexes/constraints:

- Unique constraint on `telegram_update_id`.
- Index supporting `(status, next_attempt_at, created_at)` queue selection.
- Index supporting `(reply_status, reply_next_attempt_at, created_at)` final
  delivery selection.
- Index on `video_id` for diagnostics and cached-result lookup.
- Check constraints for non-negative attempt counters when consistent with
  repository migration conventions.

The model must be imported by the model registry and the migration must be
included in packaging/build data if the repository requires explicit migration
listing.

## Intake API Contract

Add a dedicated router under `/internal/telegram-commands` protected by
`require_internal_bearer_token` only.

### `POST /internal/telegram-commands`

Request:

```json
{
  "telegram_update_id": 123456,
  "telegram_chat_id": 123,
  "telegram_user_id": 456,
  "telegram_message_id": 789,
  "telegram_chat_type": "private",
  "telegram_update_type": "message",
  "is_forwarded": false,
  "is_edited": false,
  "sender_chat_id": null,
  "text": "/summary https://youtu.be/dQw4w9WgXcQ"
}
```

Behavior:

1. Reject with `409` when commands are disabled.
  2. Require an ordinary non-forwarded, non-edited text `message`, exact private
    chat, configured chat ID, allowed user ID, and no `sender_chat_id`.
3. Return a generic `403` for unauthorized identity without revealing expected
   values and without persisting a row.
4. Parse the exact command and URL using the shared parser.
5. Persist malformed authorized `/summary` input as terminal `rejected` with a
   sanitized error so a replay produces the same result.
6. Persist valid commands as `pending`.
7. Return the existing row when `telegram_update_id` already exists; do not
   mutate its command identity or create another row.
8. Detect a replay whose update ID exists with conflicting immutable Telegram
   identity, preserve the original row, return `accepted_for_offset=true` with a
   sanitized `duplicate_conflict` outcome, emit an operational integrity alert,
   and send no user response. This impossible-provider conflict must not poison
   the Telegram offset forever.
9. Commit durable acceptance before reporting `accepted=true`.

Required response fields:

```json
{
  "request_id": 1,
  "accepted": true,
  "duplicate": false,
  "accepted_for_offset": true,
  "status": "pending",
  "acknowledged_at": null,
  "acknowledgment_required": true,
  "user_message": "Recibido. Estoy generando el resumen..."
}
```

The endpoint must not wait for metadata, transcript, inference, or final
Telegram delivery. A rejected request sets `acknowledgment_required=false`; its
stable usage/validation response is delivered later through its required
`reply_status`, using the same durable final-delivery path as other terminal
responses.

`acknowledgment_required` is authoritative for the listener:

- It is `true` only for a durably accepted non-rejected request whose
  `acknowledged_at` is null.
- It is `false` for a rejected request, a duplicate whose acknowledgment is
  already recorded, and a `duplicate_conflict` outcome.
- Duplicate responses always include the current `acknowledged_at` value so the
  listener never needs direct persistence access.

### `POST /internal/telegram-commands/{request_id}/acknowledgment`

This internal-bearer-only endpoint is mandatory for Phase 12C. It records a
successful best-effort initial acknowledgment after the listener sends it.

Request:

```json
{
  "acknowledgment_message_id": 987
}
```

- The operation is idempotent and sets `acknowledged_at` plus the optional
  acknowledgment provider message ID only once.
- Repeated calls return the existing acknowledgment state.
- It must not change request identity, processing/reply status, video, attempts,
  errors, lease fields, or final reply fields.
- A listener acknowledgment failure does not roll back durable intake and does
  not block final processing; a replay may retry acknowledgment only while
  `acknowledged_at` remains null.
- Once Telegram accepts an acknowledgment, the listener must not send it again
  within that update-handling attempt. If recording fails, retry only this API
  endpoint with the already returned provider message ID. A process crash before
  recording retains the documented unavoidable duplicate-acknowledgment window.

## URL Parsing Contract

- Parse with standard URL primitives; do not perform network requests.
- Normalize host case and allow only explicit YouTube hosts.
- Accept only `http` or `https`; prefer canonical HTTPS when storing/displaying.
- Reject URL credentials and non-default ports.
- Validate exactly 11 video-ID characters from `[A-Za-z0-9_-]`; do not decode,
  lowercase, or otherwise normalize the case-sensitive ID.
- Ignore benign tracking parameters only after the video ID is unambiguous.
- Reject a `watch` URL with missing or conflicting `v` parameters.
- Return a typed result or typed validation error suitable for API tests and
  sanitized Telegram messages.

## Metadata And Canonicalization Foundation

Phase 12A adds the service boundary and tests the behavior needed by 12B. It may
perform metadata resolution through an explicit service method or leave network
execution wired only into the future worker, but the contract must be complete:

- Use existing Google token refresh/persistence behavior.
- Call YouTube `videos.list` for one validated ID and request `snippet` plus
  `contentDetails` together.
- Count the request in the existing quota state and respect the safety stop.
- Classify YouTube outcomes explicitly: 429/5xx, temporary rate-limit reasons,
  transport failures, and malformed success payloads are retryable; no item,
  invalid ID, deleted/private video are terminal; credential failures pause for
  Google reauthentication; quota-exceeded pauses until the next quota window.
- Treat every unrecognized provider 4xx/reason as terminal by default after
  sanitization; only explicit transient, credential, quota, or rate-limit
  classifications override the default.
- Upsert `Channel` by `youtube_channel_id`.
- Upsert `Video` by `youtube_video_id` and fill missing canonical metadata.
- Classify Shorts from title/duration using reusable shared helpers rather than
  calling private polling methods.
- Do not create a channel relationship in `UserChannel`.
- Do not change uploads playlist IDs or poll markers based on guessed data.
- Do not create a canonical video when YouTube returns no accessible item.

If extracting metadata and Shorts helpers from polling is deferred to 12B, 12A
must still define typed interfaces and parser tests without duplicating product
logic.

## Non-Goals / Out Of Scope

- `getUpdates`, webhook handling, systemd units, or host installation.
- Transcript fetching, summary inference, or final Telegram reply delivery.
- Queue claiming or retry execution beyond schema/service contracts.
- Automatic Telegram stage creation.
- Multiple users/chats or an allowlist table.
- Email, mobile push, subscription, monitoring, or marker changes.

## Files To Create Or Modify

- `app/core/settings.py`.
- `.env.example`.
- `app/models/telegram_command_request.py`.
- `app/models/__init__.py` and model registry imports as required.
- `alembic/versions/<next_revision>_telegram_command_requests.py`.
- `pyproject.toml` when migration packaging uses explicit data files.
- `app/services/youtube_video_url.py` or an equivalent focused parser module.
- `app/services/on_demand_video.py` or an equivalent metadata boundary.
- `app/api/routes/telegram_commands.py`.
- `app/main.py` for router registration.
- Focused schema, parser, service, auth, and API tests.

## Tests And Verification

- [x] Settings default commands to disabled and require complete configuration
      only when enabled.
- [x] Settings/parser tests validate configured bot username normalization and
      exact case-insensitive command-suffix matching.
- [x] Migration/model tests cover all columns, FK behavior, unique constraint,
      indexes, defaults, and attempt bounds.
- [x] Parser tests cover every approved URL family and canonical output.
- [x] Parser negative tests cover lookalike hosts, credentials, ports,
      playlists, conflicting IDs, fragments, malformed IDs, and non-HTTP URLs.
- [x] Auth tests accept only the internal bearer token and reject mobile,
      missing, and invalid tokens.
- [x] Authorization tests cover wrong user, wrong chat, non-private chat, missing
      sender, and disabled commands without persistence.
- [x] Intake idempotency tests prove one row per update ID and conflict handling
      for changed immutable identity.
- [x] Command parser tests cover the exact configured bot suffix and reject an
      arbitrary suffix or forwarded command.
- [x] Rejected authorized commands persist a stable terminal result.
- [x] Acknowledgment endpoint tests prove internal-only auth, idempotency, and
      inability to mutate processing or reply state.
- [x] Intake tests prove no transcript, inference, Telegram final send, email,
      push, `UserChannel`, or marker mutation occurs.
- [x] Canonicalization tests cover existing/new channel and video, inaccessible
      video, quota accounting, and Shorts classification boundaries.
- [x] Alembic upgrade and full regression suite pass.

Suggested verification:

```bash
.venv/bin/pytest tests/test_telegram_command_intake.py
.venv/bin/pytest
.venv/bin/alembic upgrade head
git diff --check
```

## Acceptance Criteria

- [x] The authoritative feature spec is implemented only through the bounded
      Phase 12A scope.
- [x] Inbound commands are disabled by default and unsafe enabled
      configurations fail clearly.
- [x] The durable request schema and migration preserve update-level
      idempotency and future worker recovery fields.
- [x] URL parsing is strict, deterministic, and network-free.
- [x] Intake is internal-bearer-only and enforces the configured private
      chat/user identity without exposing expected IDs.
- [x] Valid requests are durably `pending`; malformed authorized requests are
      durably `rejected`; replay returns the existing request.
- [x] Canonicalization has a tested service boundary that respects Google OAuth,
      quota, canonical IDs, and Shorts classification.
- [x] No content processing, final Telegram summary, listener, webhook, email,
      push, subscription, or marker changes are implemented.
- [x] Focused tests and the full regression suite pass.

## Handoff To Phase 12B

- Phase 12B owns queue claiming, content processing, cached-summary replies,
  retries, and final delivery.
- Phase 12B must not broaden command authorization or URL support without first
  updating the authoritative feature spec.
- Phase 12C must not begin until Phase 12A and 12B each pass verification and
  human review.
