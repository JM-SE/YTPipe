# Telegram Summary Commands Specification

## Context

YTPipe currently uses the Telegram Bot API only for outbound notifications to a
single configured chat. This feature adds an inbound, single-user command path
that accepts a YouTube URL and requests the existing transcript and summary
capabilities without exposing the homelab API to the public Internet.

The first supported command is:

```text
/summary <youtube-url>
```

This specification is the product and architecture source of truth for Phases
12A, 12B, and 12C. Existing polling, subscription import, email, mobile push,
summary formatting, Shorts control, and incident-recovery contracts remain in
force unless this specification explicitly narrows behavior for a manual
command.

## Approved Architecture

```text
Telegram Bot API
        |
        | getUpdates long polling over outbound HTTPS
        v
Telegram command listener
        |
        | localhost HTTP with INTERNAL_API_BEARER_TOKEN
        v
YTPipe internal command API
        |
        | durable PostgreSQL command queue
        v
Canonical Video content processing
        |
        | request-specific Telegram reply
        v
Authorized private Telegram chat
```

- The existing bot and `TELEGRAM_BOT_TOKEN` are reused.
- Telegram updates are received with `getUpdates`, not a webhook.
- The listener and API communicate only through the protected localhost API.
- The listener must not access PostgreSQL or execute transcript/model work.
- PostgreSQL is the durable queue; Celery, Redis, and a public message broker are
  not approved.
- Exactly one `getUpdates` consumer may run for this bot.

## Command And URL Contract

- The initial command is `/summary <youtube-url>`.
- `/summary@<bot_username> <youtube-url>` is accepted when Telegram includes the
  bot suffix. The suffix must match the configured bot username
  case-insensitively; an arbitrary suffix is rejected.
- Command matching is case-sensitive and must not treat arbitrary text as a
  command.
- Exactly one YouTube video URL is accepted per command.
- The URL parser must support these canonical families:
  - `https://youtube.com/watch?v=<video_id>` and approved `www`/`m` variants;
  - `https://youtu.be/<video_id>`;
  - `https://youtube.com/shorts/<video_id>`; and
  - `https://youtube.com/live/<video_id>`.
- The parser must reject non-HTTP(S) schemes, credentials in URLs, unexpected
  ports, lookalike hosts, channel/playlist-only URLs, missing IDs, conflicting
  IDs, and malformed video IDs.
- URL parsing is local. The supplied URL must not be fetched and redirects must
  not be followed.
- An authorized malformed `/summary` command receives a stable usage or URL
  validation response.
- Forwarded messages are not commands even when their text starts with
  `/summary`.
- Unknown commands may receive concise usage guidance but must not enter the
  summary queue.

## Authorization Contract

- Commands are disabled by default.
- Commands require all existing outbound Telegram configuration plus an
  explicit allowed Telegram user ID.
- A command is authorized only when all of these conditions hold:
  - `TELEGRAM_COMMANDS_ENABLED=true`;
  - the update contains a normal `message` with text;
  - `message.chat.type` is `private`;
  - `message.chat.id` exactly matches `TELEGRAM_CHAT_ID`;
  - `message.from.id` exactly matches `TELEGRAM_ALLOWED_USER_ID`; and
  - the message is not forwarded, edited, or posted on behalf of a channel.
- Authorization is checked by the listener and repeated by the internal API as
  defense in depth.
- Unauthorized updates are silently ignored and confirmed only by advancing the
  Telegram offset; no message is sent to them. They must not create database
  rows, invoke YouTube, fetch transcripts, run model inference, or reveal the
  configured IDs.
- The internal intake and worker endpoints accept
  `INTERNAL_API_BEARER_TOKEN` only. A mobile bearer token is not sufficient.
- Bot tokens, bearer tokens, raw provider responses, internal URLs, stack
  traces, and unsanitized exceptions must not be logged, persisted, or sent to
  Telegram.

## Intake And User Experience

1. The listener receives a Telegram update and validates its basic shape and
   authorization.
2. The listener submits the update metadata and command text to the protected
   intake endpoint.
3. The API idempotently persists one command request by `telegram_update_id`.
4. The user receives a short acknowledgment such as `Recibido. Estoy generando
   el resumen...` after durable acceptance.
5. The listener advances the Telegram offset only after durable acceptance or
   a deliberate terminal ignore/rejection decision.
6. Processing occurs independently from the long-poll intake loop.
7. The final summary or a sanitized terminal failure is sent as a reply to the
   original Telegram message when possible.

Acknowledgment delivery is best effort. A crash between Telegram accepting the
acknowledgment and YTPipe recording it may produce a duplicate acknowledgment;
Telegram provides no send-side idempotency key. This does not permit duplicate
command rows or duplicate transcript/summary generation.

## Canonical Video And Metadata Rules

- The API extracts the video ID, then resolves authoritative metadata through
  the existing Google OAuth credentials and YouTube Data API.
- One metadata call should request the required `snippet` and `contentDetails`
  parts together when practical.
- Metadata resolution supplies the canonical video title, publication time,
  channel ID, channel title, and duration.
- Existing `Channel` and `Video` rows are reused by their YouTube identifiers.
- A previously unknown channel is persisted as a canonical `Channel` with a
  nullable uploads playlist ID.
- A manual command must not create a `UserChannel`, enable channel monitoring,
  modify `last_seen_video_id`, or otherwise change subscription state.
- A manual command must not create `NotificationDelivery` rows, send email, or
  fan out mobile push notifications.
- Missing, private, deleted, inaccessible, or malformed video metadata produces
  a sanitized failure and no fabricated canonical record.
- YouTube API usage participates in the existing quota estimate and safety-stop
  contract; manual commands do not bypass quota controls.

## Content Processing Rules

- Transcript and summary are canonical video content and remain stored on
  `Video.transcript` and `Video.summary`.
- Existing canonical transcript or summary content is reused.
- A repeated, distinct command for a video with an existing summary sends that
  cached summary again without fetching the transcript or running inference.
- The same Telegram update replayed by Telegram must return the existing command
  request and must not create another reply intent.
- Transcript and summary processing reuse the existing retry limits,
  summarization circuit breaker, llama.cpp recovery, output format, and
  single-inference guarantees.
- Manual processing creates or reuses only the shared transcript and summary
  content stages. It must not depend on the one-per-video automatic `telegram`
  pipeline stage for the command reply.
- Automatic poll/startup/reconciliation drains must not discover a manual-only
  content stage and create an unsolicited automatic `telegram` or
  `fallback_telegram` stage. Automatic drains may process shared content only
  when an automatic Telegram/fallback intent already exists for that video.
- The response for each command is represented by its command-request row so
  distinct repeated commands can each receive the cached result.
- A terminal shared content stage is not automatically reopened by a repeated
  command. Reopening terminal content work requires a separately approved
  recovery contract.

## Shorts Rules

- Manual metadata resolution still classifies `Video.is_short` using the
  existing title/duration contract.
- `SHORTS_PROCESSING_ENABLED=false` blocks manual transcript, summary, and
  Telegram summary delivery for a classified Short.
- The command receives a sanitized terminal response explaining that Short
  processing is disabled.
- `is_short=None` remains unknown and is not treated as a confirmed Short.
- A later manual request while Shorts processing is enabled may process a Short
  only if doing so does not reopen terminally skipped shared stages.
- The feature does not automatically backfill Shorts skipped while disabled.

## Durable Request And Idempotency Contract

- Every authorized `/summary` update has one durable command-request row.
- `telegram_update_id` is globally unique for this single bot.
- A request records its Telegram identity, normalized video identity when
  available, canonical `Video` reference when available, processing state,
  attempts, sanitized error, acknowledgment state, and a separate final reply
  state.
- Approved request statuses are `pending`, `processing`, `pending_retry`,
  `completed`, `failed`, and `rejected`.
- `completed` means summary content is available for this request; it does not
  imply that Telegram accepted the final reply.
- `rejected` is a terminal validation or policy outcome, including one
  discovered after metadata resolution such as a disabled classified Short.
- `failed` is a terminal metadata or content-processing failure after approved
  retries.
- Approved final reply statuses are `pending`, `sending`, `pending_retry`,
  `sent`, `failed`, and `suppressed`.
- Final summary, validation, blocked-Short, and processing-failure messages all
  use the separate reply state. A request is fully settled only when its
  processing status is terminal and its reply status is `sent`, `failed`, or
  `suppressed`.
- Queue selection includes non-terminal processing work and terminal processing
  rows whose reply remains due.
- A worker must durably claim a request before external metadata, transcript,
  model, or Telegram work.
- Claims have an opaque fencing token plus an expiry. A reclaimed request gets a
  new token. Fencing prevents a worker known to be stale from persisting state or
  dispatching a reply after a failed ownership recheck; it cannot eliminate the
  provider-side crash/disconnect window after the final check.
- A PostgreSQL-backed global execution lock is held while command content work
  or reply delivery is active. Polling, reconciliation, startup content work,
  and command processing share this lock so an expired lease cannot be reclaimed
  while the previous database session still owns active work.
- Retry scheduling must avoid a tight loop while quota, Google reauthentication,
  or the summarization circuit is paused.
- Telegram delivery remains externally at-least-once. Internal uniqueness and
  durable state minimize duplicates but cannot eliminate a crash window after
  Telegram accepts a message and before the database commit.
- Disabling commands pauses accepted work and sends nothing until re-enabled.
  Changing the configured chat/user identity suppresses delivery for requests
  accepted under the old identity; those rows remain durable for diagnostics.

## Failure Classification

- Invalid command, invalid URL, inaccessible video, blocked Short, and permanent
  Telegram 4xx responses except 429 are terminal.
- YouTube/Google transport failures, YouTube 429/5xx and temporary rate-limit
  reasons, Telegram timeout/429/5xx responses, and transient local API failures
  are retryable within the approved attempt limit.
- YouTube invalid-ID/no-item/deleted/private outcomes are terminal. Google
  credential failures pause for reauthentication, and quota-exceeded reasons
  pause until the quota window can be retried. Malformed successful provider
  payloads are retryable up to the request-level limit.
- Unrecognized YouTube 4xx responses or reason codes are terminal by default
  after sanitization. Only explicitly classified credential, quota, rate-limit,
  or transient cases override that default.
- A Google reauthentication requirement pauses the request without consuming
  attempts until credentials are repaired or an explicit terminal policy is
  reached.
- An open summarization circuit leaves the request retryable without starting
  new inference or exhausting attempts merely because the circuit is open.
- A confirmed transcript-unavailable/zero-snippet outcome is non-retryable as
  required by `specs/retry_and_deduplication_rules.spec.md`; transport and
  provider failures remain retryable. Phase 12B must introduce typed outcomes so
  the implementation no longer conflates these cases.
- User-facing failures are concise Spanish messages and contain no internal
  diagnostics.

## Long Polling Contract

- The listener calls `getUpdates` with `timeout=50`, `limit=100`, and
  `allowed_updates=["message"]`.
- The HTTP read timeout is greater than the Telegram long-poll timeout, with a
  target of 60 to 65 seconds.
- Telegram returns immediately when an update exists. With no updates, the
  request returns after about 50 seconds and the listener immediately opens the
  next request.
- Successful empty responses have no additional sleep.
- Transport/provider failures use bounded exponential backoff, targeting
  1, 2, 5, 10, and at most 30 seconds.
- A higher `offset` confirms all lower update IDs. The listener must not advance
  beyond an authorized command until the API has durably accepted or terminally
  rejected it.
- Telegram retains unconsumed updates for no longer than its provider-defined
  retention window, currently up to 24 hours.
- `getUpdates` and webhooks are mutually exclusive. Rollout removes any webhook
  without dropping pending updates during routine starts.
- Historical updates may be dropped only once through an explicit operator
  rollout command. Routine restarts must never use `drop_pending_updates=true`.

## Non-Goals

- Multiple Telegram users, chats, tenants, or role management.
- Public Telegram webhook infrastructure.
- Public API exposure, domain, TLS termination, or tunnel setup.
- Telegram media, voice, callback-query, inline-query, edited-message, or
  channel-post handling.
- Commands other than `/summary` beyond optional usage guidance.
- Automatic subscriptions or monitoring changes from submitted videos.
- Email or mobile push delivery for manual commands.
- Regenerating an existing summary or storing summary versions.
- Celery, Redis, a new broker, a Telegram SDK, or a YouTube downloader.
- UI or mobile-app changes.

## Acceptance Criteria

- [ ] One authorized private user can submit `/summary <youtube-url>` through the
      existing bot and receive an acknowledgment plus a final result.
- [ ] No public inbound endpoint is required because updates use long polling.
- [ ] Unauthorized updates cause no persisted request or downstream work.
- [ ] Supported YouTube URL forms resolve to one validated video ID without
      fetching the submitted URL.
- [ ] Arbitrary-channel videos create canonical `Channel`/`Video` rows without
      creating `UserChannel`, email, push, or marker changes.
- [ ] Transcript and summary processing reuse existing canonical content,
      retries, circuit breaker, and output format.
- [ ] Distinct repeated commands resend the cached summary without regeneration.
- [ ] Replayed Telegram updates are idempotent by `telegram_update_id`.
- [ ] Manual commands obey YouTube quota controls and the global Shorts flag.
- [ ] Command replies are request-specific and do not misuse the automatic
      per-video Telegram pipeline stage.
- [ ] Pending work survives API/listener restarts and stale claims recover.
- [ ] User-facing errors and logs contain no secrets or internal diagnostics.
- [ ] Long polling is single-consumer, near-real-time, and does not expose the
      homelab to inbound Internet traffic.
