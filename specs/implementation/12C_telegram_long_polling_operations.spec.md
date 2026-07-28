# Backend Phase 12C Telegram Long Polling And Operations Specification

## Context

Phase 12C completes the inbound command feature by adding the single-consumer
Telegram `getUpdates` listener, an independent local worker trigger loop,
systemd operation, command registration, rollout instructions, observability,
and end-to-end tests. It consumes only the internal APIs approved in Phases 12A
and 12B and must not access application persistence directly.

## Requirements

- [ ] Add one listener executable that receives Telegram updates with long
      polling over outbound HTTPS.
- [ ] Keep intake active while the independent worker trigger waits for a
      long-running `process-next` API call.
- [ ] Validate private chat and allowed user before submitting an update.
- [ ] Submit authorized commands only to the localhost internal API with the
      internal bearer token.
- [ ] Advance Telegram offsets only after durable API acceptance or deliberate
      terminal ignore/rejection.
- [ ] Run exactly one `getUpdates` consumer for the bot.
- [ ] Add a systemd service with safe restart behavior and no embedded secrets.
- [ ] Add explicit webhook removal and initial pending-update rollout guidance.
- [ ] Add bounded retries/backoff and sanitized logging.
- [ ] Register `/summary` as the initial bot command through an idempotent
      operator step or startup operation.
- [ ] Verify end-to-end behavior against a real bot, local API, PostgreSQL,
      YouTube metadata, transcript source, llama.cpp, and Telegram reply.

## Listener Process Design

Suggested files:

```text
scripts/ytpipe-telegram-command-listener.py
systemd/ytpipe-telegram-command-listener.service
```

The process contains two independent loops in one supervised service:

1. Intake loop: owns the only `getUpdates` stream, validates updates, posts
   commands to the intake API, sends the acknowledgment requested by the intake
   response, records it through the mandatory 12A acknowledgment endpoint, and
   advances the offset.
2. Worker trigger loop: wakes immediately after accepted work and periodically
   for recovery, calls `POST /internal/telegram-commands/process-next`, and
   continues until the API reports no due work.

The loops may use threads or an async design, but failures in one loop must be
visible and must not silently leave the service healthy while all useful work
has stopped. The design must prevent more than one intake loop in one process.

The listener must not:

- import SQLAlchemy models or open a database connection;
- fetch YouTube metadata or transcripts;
- call llama.cpp;
- implement pipeline business logic;
- bind a public HTTP port; or
- accept commands through a webhook.

## Long Polling Contract

Use Telegram Bot API `getUpdates` with:

```json
{
  "offset": "<next update id>",
  "limit": 100,
  "timeout": 50,
  "allowed_updates": ["message"]
}
```

- The request returns immediately when an update exists.
- With no update, Telegram returns after approximately 50 seconds and the
  listener immediately issues the next request.
- The HTTP connect/read timeout must exceed the 50-second provider timeout;
  target a 60-to-65-second read timeout.
- Do not sleep after successful empty responses.
- On timeout, transport failure, HTTP 429, or 5xx, retry with bounded backoff of
  approximately 1, 2, 5, 10, then at most 30 seconds.
- Honor Telegram `retry_after` when present, bounded to avoid an uninterruptible
  service.
- Non-retryable Bot API errors must be logged without token-bearing URLs or raw
  sensitive response bodies and must cause a visible unhealthy/restart outcome
  when continuing would lose updates.
- Process a returned batch in ascending `update_id` order.
- Set the next offset to `update_id + 1` only after that update is safely
  handled.
- Do not skip a later update over an unaccepted authorized `/summary` command.
- Updates other than normal text `message` objects are terminally ignored and
  may advance the offset.

## Update Handling

- Recheck `TELEGRAM_COMMANDS_ENABLED` before useful work.
- Require the configured private chat and allowed sender.
- Silently ignore unauthorized updates and never send them policy details.
- Submit authorized `/summary` commands to the Phase 12A intake endpoint.
- Treat an idempotent duplicate intake response as durably accepted.
- Send the acknowledgment only when `acknowledgment_required=true` and
  the intake response reports `acknowledged_at=null`, then record the returned provider message ID
  through the Phase 12A acknowledgment endpoint.
- Retry a failed acknowledgment with bounded Telegram backoff while handling the
  current update. If it still fails, advance after durable intake so
  acknowledgment failure cannot block final processing or later updates.
- Once Telegram accepts the acknowledgment, do not send it again within the
  current handling attempt. Retry only the acknowledgment-recording endpoint if
  that local API call fails.
- For malformed authorized `/summary`, let the durable final-reply queue deliver
  the stable validation response and advance after the API reports durable
  rejection.
- Unknown authorized commands may receive concise `/summary <youtube-url>` usage
  without entering the durable queue.
- Edited messages, channel posts, callback queries, inline queries, media-only
  messages, and forwarded content do not create commands in v1.
- A `duplicate_conflict` response is an integrity alert but is
  `accepted_for_offset=true`: preserve the original row, send no user message,
  log no identities, and advance so later legitimate updates are not poisoned.
- Never trust an inbound destination for outbound sends without checking the
  configured chat.

Telegram stores unconsumed updates for a provider-defined maximum, currently up
to 24 hours. The listener must resume promptly after outages, but the database
queue, not Telegram retention, is the durable source after intake acceptance.

## Worker Trigger Loop

- Wake immediately after the intake API accepts new pending work.
- Also wake at least every 30 seconds to recover due retries, stale claims, or
  work accepted before a listener restart.
- Call only the protected localhost `process-next` endpoint.
- After a claimed request completes, call again while the API reports due work.
- When no due work exists, return to the event/periodic wait.
- Treat `409 busy` as normal contention and retry after bounded delay.
- Use a client timeout suitable for the longest healthy one-video processing
  call; document that long transcripts can require multiple sequential model
  requests.
- A local client timeout must not immediately resubmit the same work as if it
  were unclaimed. Let the durable lease/state determine recovery.
- API unavailability uses bounded backoff and does not stop the intake loop from
  receiving and durably submitting commands when intake is still reachable.

## Telegram Mode And Bot Command Registration

Telegram webhooks and `getUpdates` are mutually exclusive.

- The rollout must call `deleteWebhook` before enabling the listener.
- Routine startup may idempotently ensure the webhook is absent with
  `drop_pending_updates=false`.
- `drop_pending_updates=true` is permitted only as an explicit one-time operator
  choice during initial rollout, before users are told the command is active.
- Routine restart, upgrade, and recovery commands must never drop pending
  updates.
- Register this command with `setMyCommands`:

```text
summary - Resume un video de YouTube
```

- Command registration is user-interface metadata, not authorization. The
  listener/API identity checks remain mandatory.

## systemd Contract

The unit must:

- run as the existing unprivileged application user;
- use `/home/jmse/labs/YTPipe` as its working directory;
- read secrets from the existing protected `.env` mechanism rather than the
  unit body or command line;
- start after network availability and `ytpipe-api.service` when that unit is
  installed;
- use `Restart=on-failure` with a bounded restart delay;
- stop cleanly, cancel long polling, and join both loops;
- avoid shell interpolation of bot or bearer tokens;
- produce useful sanitized journal logs; and
- never require sudo during normal operation.

Only one installed/enabled listener service, manual listener, or other
`getUpdates` consumer may exist. Rollout instructions must explicitly detect and
stop duplicates before enablement.

## Configuration And Secrets

The listener reads:

- `TELEGRAM_COMMANDS_ENABLED`;
- `TELEGRAM_NOTIFICATIONS_ENABLED`;
- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_CHAT_ID`;
- `TELEGRAM_ALLOWED_USER_ID`;
- `TELEGRAM_BOT_USERNAME`;
- `INTERNAL_API_BEARER_TOKEN`; and
- `APP_PORT` for localhost API calls.

It must construct the API origin as loopback rather than using a public host.
No bot token or bearer token may appear in process arguments, unit files,
journal messages, exception strings, health output, or diagnostics.

## Operational Observability

Sanitized logs should include:

- listener start/stop and enabled/disabled state;
- Bot API authentication validation success without bot token;
- last safely handled update ID;
- intake accepted/duplicate/rejected outcome by internal request ID;
- worker claimed/no-work/busy outcome;
- retry category and delay without sensitive payloads; and
- fatal configuration or mutually exclusive webhook errors.

Do not log full command text from unauthorized users. For authorized commands,
prefer request ID and normalized YouTube video ID over the original URL.

A lightweight process/service health check may be documented, but no public
health endpoint is required. Database-backed command diagnostics remain in the
API path from Phase 12B.

## Rollout Procedure

The operator handoff must include:

1. Back up PostgreSQL and apply the 12A migration.
2. Configure `TELEGRAM_ALLOWED_USER_ID` and `TELEGRAM_BOT_USERNAME`, and keep
   commands disabled.
3. Validate bot authentication with `getMe` without logging the token. Confirm
   that the returned username matches `TELEGRAM_BOT_USERNAME`
   case-insensitively.
4. Stop any other webhook or `getUpdates` consumer.
5. Delete the webhook with `drop_pending_updates=false`, or explicitly choose a
   one-time initial drop before activation.
6. Register `/summary` with `setMyCommands`.
7. Run the listener manually with commands still scoped to the authorized
   private chat.
8. Enable `TELEGRAM_COMMANDS_ENABLED=true`, restart API/listener, and verify
   configuration validation.
9. Test malformed URL, unauthorized sender, valid uncached video, cached repeat,
   disabled Short, transcript unavailable, and listener/API restart recovery.
10. Install/enable the systemd unit only after manual validation.
11. Reboot-test API, listener, PostgreSQL, and llama.cpp recovery together.

## Rollback Procedure

1. Set `TELEGRAM_COMMANDS_ENABLED=false` and restart the API so new intake is
   rejected before stopping the listener.
2. Stop and disable `ytpipe-telegram-command-listener.service`.
3. Leave already accepted command rows intact for diagnostics; do not delete or
   rewrite their processing/reply state automatically.
4. Do not use `drop_pending_updates=true` during rollback. Document whether
   Telegram updates accumulated after disablement will be intentionally ignored
   or processed before a later re-enable.
5. Leave the webhook absent unless a separately approved webhook consumer is
   being restored. Never enable webhook and long polling concurrently.
6. Keep the 12A migration applied unless a separately reviewed database
   downgrade is required; feature disablement does not require schema rollback.
7. Verify that existing outbound Telegram notifications, polling, email, mobile
   push, and llama.cpp recovery continue normally.

## Non-Goals / Out Of Scope

- Public webhook, reverse proxy, domain, TLS, or tunnel deployment.
- Multiple bots, users, chats, or workers consuming `getUpdates`.
- Database access or content processing in the listener.
- New commands, Telegram media, callback buttons, or conversational state.
- Celery, Redis, a broker, Telegram SDK, container orchestration, or cloud
  service.
- Changes to email, mobile push, Activity, subscriptions, or polling markers.

## Files To Create Or Modify

- `scripts/ytpipe-telegram-command-listener.py`.
- `systemd/ytpipe-telegram-command-listener.service`.
- `systemd/README.md` with install, rollout, validation, disable, and recovery
  commands.
- `.env.example` only if 12A documentation needs operational clarification.
- Focused listener unit tests using mocked Telegram and localhost API responses.
- End-to-end/manual smoke-test documentation.

## Automated Tests

- [ ] Long polling uses `timeout=50`, `limit=100`, and only `message` updates.
- [ ] A message causes immediate handling without waiting for the timeout.
- [ ] Empty success immediately opens the next long poll.
- [ ] Update batches process in ascending order and offset advances one handled
      update at a time.
- [ ] Authorized intake failure does not advance past the command.
- [ ] Duplicate intake response safely advances without another command row.
- [ ] A conflicting replay raises a sanitized integrity signal, preserves the
      original row, sends no reply, and advances without poisoning later updates.
- [ ] Unauthorized, non-private, edited, callback, channel, and media-only
      updates create no command work.
- [ ] Forwarded `/summary` and an arbitrary `@bot_username` suffix create no
      command work; the configured suffix is accepted case-insensitively.
- [ ] Retry tests cover timeout, transport, 429 with `retry_after`, 5xx, and
      bounded backoff.
- [ ] Logs and raised errors do not contain bot or bearer tokens.
- [ ] Intake remains responsive while the worker trigger is blocked on a mocked
      long processing request.
- [ ] Worker wakes after intake and periodically, drains until no work, handles
      busy, and does not immediately duplicate timed-out processing.
- [ ] Startup never drops pending updates during routine operation.
- [ ] Acknowledgment success is recorded through the mandatory 12A endpoint and
      duplicate updates do not resend it after `acknowledged_at` is present.
- [ ] Unit-file tests or review prove secrets are absent and restart/user/order
      settings match the contract.
- [ ] Full regression suite passes.

## Manual End-To-End Acceptance

- [ ] `/summary <valid uncached URL>` receives acknowledgment and formatted
      summary in the authorized private chat.
- [ ] The created video/channel are canonical, with no `UserChannel`, email,
      push, or marker side effects.
- [ ] Repeating the command resends the cached summary without transcript or
      llama.cpp work.
- [ ] Replaying the same update does not create another request or final reply
      intent.
- [ ] Unauthorized account/chat receives no response and causes no work.
- [ ] A disabled Short receives the policy response and causes no content work.
- [ ] Restarting the listener after durable intake still completes the command.
- [ ] Restarting the API during processing recovers through lease/retry state.
- [ ] New commands are accepted while another long summary is processing.
- [ ] No public port, webhook, external queue, or new cloud dependency is used.

## Acceptance Criteria

- [ ] One supervised listener is the only `getUpdates` consumer for the bot.
- [ ] Long polling is near-real-time, uses the approved 50-second timeout, and
      consumes negligible idle resources.
- [ ] Intake and worker triggering remain independent during long processing.
- [ ] Offsets advance only after safe handling and routine restarts never drop
      pending updates.
- [ ] The systemd unit is safe, unprivileged, restartable, and contains no
      secrets.
- [ ] Rollout and rollback instructions are complete and manually validated.
- [ ] Bot command registration, authorization, durable recovery, cached repeat,
      Shorts behavior, and end-to-end summary delivery pass verification.
- [ ] No webhook/public exposure, Redis, Celery, Telegram SDK, extra user scope,
      or unrelated product behavior is added.

## Phase Completion Gate

Phase 12C completes the feature only after automated verification, the manual
checklist, an end-of-phase review, and human approval. Any need for multi-user
authorization, new commands, webhook deployment, terminal-stage reopening, or a
different queue architecture requires a new spec rather than an unreviewed
extension of this phase.
