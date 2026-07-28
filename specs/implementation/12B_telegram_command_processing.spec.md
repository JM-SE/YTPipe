# Backend Phase 12B Telegram Command Processing And Reply Specification

## Context

Phase 12B consumes the durable requests created by Phase 12A and completes the
on-demand summary workflow defined in
`specs/telegram_summary_commands.spec.md`. It adds queue claiming, canonical
content processing, cached-summary reuse, request-specific Telegram replies,
retry behavior, and recovery. It does not add the `getUpdates` listener or host
unit; those belong to Phase 12C.

## Requirements

- [ ] Claim and process at most one eligible command request per worker call.
- [ ] Commit the claim before external work and recover stale claims.
- [ ] Resolve metadata and canonical `Channel`/`Video` records through the 12A
      service boundary.
- [ ] Reuse or refactor existing transcript/summary pipeline behavior rather
      than duplicate inference, retry, circuit-breaker, or Shorts logic.
- [ ] Create/reuse shared transcript and summary stages without requiring a new
      automatic `telegram` stage for a manual command.
- [ ] Reuse cached `Video.summary` without transcript or inference calls.
- [ ] Send one request-specific final reply for each distinct command request.
- [ ] Reply to the original Telegram message when Telegram accepts reply
      parameters.
- [ ] Persist sanitized success/failure/retry state and provider reply message
      ID when available.
- [ ] Preserve existing polling, reconciliation, startup, email, push, and
      automatic Telegram behavior.
- [ ] Add the bounded PostgreSQL-backed global execution lock required to
      coordinate command work with polling, reconciliation, startup, and normal
      pipeline drains across processes.

## Internal Worker API

Add an internal-bearer-only endpoint such as:

```http
POST /internal/telegram-commands/process-next
Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>
```

The endpoint processes zero or one request and returns a stable outcome:

```json
{
  "claimed": true,
  "request_id": 1,
  "status": "completed",
  "work_remaining": false
}
```

- Return `claimed=false` and `200` when no eligible work exists.
- Return `409` when another command/poll/reconciliation execution owns the
  approved shared execution lock.
- Do not use the mobile/admin bearer union; require the internal token.
- The endpoint may remain synchronous after claiming one request because the
  caller is the dedicated worker loop, not Telegram's long-poll connection.
- Client timeout and operational docs must account for multi-call summary
  generation on long transcripts.

## Claim And Recovery Contract

1. Acquire the shared PostgreSQL execution lock non-blockingly; return busy if
   another entry point owns it.
2. Recover `processing` rows and `reply_status=sending` rows whose lease expired
   only after acquiring the shared
   PostgreSQL execution lock, then move them to `pending_retry` with a sanitized
   recovery reason in the applicable processing or reply state.
3. Select one oldest-due work item: either a `pending`/due `pending_retry`
   processing request or a terminal processing row with `reply_status=pending`
   or due `reply_status=pending_retry`.
4. For processing work, atomically set status `processing` and
   `processing_started_at`. For reply work, retain the terminal processing status
   and atomically set `reply_status=sending` plus `reply_started_at`.
5. In either case, set a bounded `lease_expires_at`, generate a new opaque
   `lease_token`, increment only the applicable attempt counter when actual work
   is about to occur, and commit before external calls.
6. A concurrent worker must not claim the same row.
7. Hold a session-level PostgreSQL advisory lock such as
   `pg_try_advisory_lock`, not a transaction-level advisory lock, for the full
   external-work window. Use a dedicated/pinned connection that is not returned
   to the SQLAlchemy pool when the claim transaction commits;
   polling, reconciliation, startup content processing, and command processing
   must all acquire the same lock. Process-local locks remain optional secondary
   guards only.
8. Every post-claim mutation and every final Telegram send must recheck the
   current `lease_token`. Reclaim changes the token, fencing a worker already
   known to be stale. A database disconnect after the final check retains the
   documented external at-least-once delivery window.
9. Refresh or size the lease so a valid long-transcript inference is not treated
   as stale while still running. The advisory lock prevents reclaim while the
   original database session is alive even if the timestamp expires.
10. Persist final state in a new transaction even when provider calls fail, then
    explicitly release the advisory lock in a `finally` path before returning
    the dedicated connection.

The implementation may introduce a focused command queue service. Queue SQL,
claim state transitions, and provider orchestration must not live directly in
the route handler.

## Shared Content Processing Contract

Refactor `PipelineService` only as needed to expose a reusable content-only
operation with these properties:

- It creates/reuses `transcript` and `summary` stages for the canonical user and
  video.
- Existing scheduled pipeline creation still creates `transcript`, `summary`,
  and automatic `telegram` stages exactly as before.
- Manual content processing does not create or depend on the automatic
  `telegram` or `fallback_telegram` stage for its final response.
- It preserves the existing one-summary-inference lock, attempt counts,
  dependency rules, summarization circuit, summary output format, llama.cpp
  restart integration, and `Video.transcript`/`Video.summary` storage.
- It introduces typed transcript outcomes that distinguish permanent
  unavailable/zero-snippet results from retryable transport/provider failures,
  then applies that distinction consistently to manual and existing automatic
  pipeline paths as required by the authoritative retry spec.
- If `Video.summary` already exists, no transcript, YouTube Transcript API, or
  llama.cpp call occurs.
- If transcript exists but summary does not, transcript is reused.
- Existing terminal shared stages are not silently reset or reopened.
- A confirmed disabled Short creates no new content stages and makes no
  transcript/model call.
- Automatic drains may process shared content only when an automatic `telegram`
  or `fallback_telegram` intent already exists for the same user/video. A
  manual-only transcript/summary stage is advanced only by the command worker.
- Startup, `process_pending_stages`, `process_next_pending_video`, and drain
  queries must all enforce that origin rule and must never create an automatic
  delivery intent merely because manual content work exists.

If a generalized content service is extracted from `PipelineService`, existing
polling/startup/reconciliation paths must use or remain behaviorally equivalent
to it and their tests must remain unchanged.

## Request Processing Flow

For a claimed request:

1. Revalidate the command feature and current private chat/user policy.
2. Parse/reuse the normalized video ID.
3. Check quota safety and resolve YouTube metadata.
4. Upsert canonical channel/video without creating subscription state.
5. Persist `request.video_id` and Short classification.
6. If a classified Short is disabled, mark processing `rejected`, leave the
   policy reply due, and perform no content work.
7. If `Video.summary` exists, mark processing `completed` and leave the cached
   summary reply due.
8. Otherwise advance transcript and summary content stages.
9. If content remains retryable or paused, set `pending_retry` with an
   appropriate `next_attempt_at` and do not send a false terminal failure.
10. If content fails terminally, mark processing `failed` and leave the separate
    final reply due with a sanitized failure response.
11. If summary succeeds, mark processing `completed` and leave the separate
    request-specific summary reply due.

The command path must not create email/activity `NotificationDelivery`, mobile
push delivery, `UserChannel`, or channel marker changes.

## Processing And Reply State Table

| Processing status | Meaning | Required reply behavior |
| --- | --- | --- |
| `pending` | Accepted and eligible for request-level work. | `reply_status=pending`; no final reply yet. |
| `processing` | Currently owned by one fenced worker. | Final reply remains pending. |
| `pending_retry` | Retryable work or an operational pause. | Do not send a false terminal response. |
| `completed` | Summary is available, including cached summary. | Deliver summary until reply is `sent` or terminally `failed`. |
| `rejected` | Validation or policy rejection, including disabled Short. | Deliver stable validation/policy response. |
| `failed` | Terminal metadata/content failure. | Deliver sanitized failure response. |

Reply transitions are independent:

```text
pending -> sending -> sent
pending -> sending -> pending_retry -> sending
pending -> sending -> failed
pending -> suppressed
```

Queue selection must include due processing statuses and any `completed`,
`rejected`, or `failed` row whose reply status is `pending` or due
`pending_retry`. `reply_max_attempts=3` is the deterministic default. A request
is fully settled only when processing is terminal and reply is `sent` or
terminally `failed`/`suppressed`.

Before claiming work, revalidate runtime policy:

- If `TELEGRAM_COMMANDS_ENABLED=false`, return `commands_disabled`, claim
  nothing, consume no attempt, and send no acknowledgment/final reply. Accepted
  rows remain in their existing state and become eligible after re-enable.
- If the persisted request chat/user no longer matches the configured authorized
  identity, mark non-terminal processing `rejected` as needed and set
  `reply_status=suppressed`. Never send to either the old or new identity for
  that request.
- Terminal rows whose destination no longer matches are also set to
  `reply_status=suppressed` without a provider call.

## Telegram Reply Contract

Extend the Telegram service through a reusable destination-aware method rather
than duplicating raw `httpx` calls.

- Require the destination chat to match the configured authorized chat.
- Accept optional original `message_id` and send it using Telegram
  `reply_parameters`.
- Preserve current plain-text rendering and safe summary truncation below the
  Telegram message limit.
- Return a typed result containing the provider message ID when available.
- Preserve existing timeout/transport/429/5xx retry classification and
  permanent non-429 4xx classification.
- Parse and honor Telegram `retry_after` when available, bounded by an approved
  maximum.
- Do not expose the bot token through request logging because the token appears
  in the Bot API URL.
- Existing `send_message` and automatic `send_video_notification` callers must
  remain backward compatible.

Suggested Spanish outcomes include:

```text
Recibido. Estoy generando el resumen...
Este video es un Short y su procesamiento esta desactivado.
No se pudo obtener una transcripcion para este video.
El resumen esta temporalmente pendiente porque el servicio local no esta disponible.
```

Exact wording may be refined, but messages must remain stable, concise, and free
of internal diagnostics.

## Retry And Pause Rules

- Command work has at most three actual processing attempts by default.
- `TelegramCommandRequest.attempt_count/max_attempts` applies to request-level
  metadata/canonicalization failures before content is available.
- `PipelineStage.attempt_count/max_attempts` remains authoritative for shared
  transcript and summary attempts. Merely waiting for shared content does not
  consume a request-level attempt.
- Telegram final reply retries are counted separately in
  `reply_attempt_count` with `reply_max_attempts=3`, so a successful expensive
  summary is never regenerated because delivery failed.
- Cached summaries are never regenerated during a delivery retry.
- YouTube/Google transport errors and Telegram timeout/429/5xx are retryable.
- Invalid URL, inaccessible video, blocked Short, unavailable terminal content,
  exhausted shared stage, and permanent Telegram 4xx are terminal.
- YouTube 429/5xx, temporary rate-limit reasons, transport failures, and
  malformed success payloads are retryable. No-item/deleted/private/invalid-ID
  outcomes are terminal. Credential errors pause for reauthentication and quota
  exhaustion pauses until the quota window resets without consuming attempts
  merely for remaining paused.
- Every unrecognized YouTube 4xx/reason is terminal by default after
  sanitization. Explicit credential, quota, rate-limit, and transient
  classifications are the only overrides.
- Google reauthentication pauses the request without burning repeated attempts;
  schedule a later retry and reuse existing operational alert behavior.
- An open summary circuit pauses the request without inference and without
  exhausting attempts merely because the circuit is still open.
- Quota safety block schedules the next eligible retry at or after the quota-day
  reset rather than polling in a tight loop.
- Retry timestamps must make the worker idle when no request is due.
- Terminal failure response delivery is itself retryable for retryable Telegram
  errors. A request is not considered fully communicated until final success or
  permanent/exhausted reply failure is durably recorded.

## Shared-Content Leader Rule

- The oldest unsettled command for a normalized `youtube_video_id`, or later the
  canonical `video_id`, is the command leader while transcript/summary content
  is missing.
- Only the leader may trigger a shared transcript or summary stage attempt.
- Sibling requests wait in `pending_retry` without incrementing request or stage
  attempts and use a `next_attempt_at` no earlier than the leader's due time.
- When content completes, every sibling becomes eligible to deliver the same
  cached summary independently.
- When a shared content stage reaches terminal failure, siblings inherit the
  same sanitized terminal content outcome without consuming additional shared
  attempts.
- Automatic pipeline work may complete the shared stages while commands wait;
  waiting commands then use the cached result. It may not bypass the global
  execution lock.

## Concurrency

- Command processing shares the same high-level execution exclusion as polling
  and reconciliation so canonicalization and pipeline drains do not overlap
  unsafely.
- At most one llama.cpp inference may run across all entry points.
- Add a PostgreSQL-backed exclusion/claim where needed; do not rely solely on a
  separate listener-process lock.
- This phase explicitly implements the minimum cross-process advisory-lock
  mitigation identified in `specs/architectural_risks_followup.spec.md` for
  polling, reconciliation, startup content work, and command processing. It does
  not add a general multi-worker lease model to unrelated tables.
- A busy outcome is not a failed command. The worker retries after bounded
  delay.
- Two requests for the same video must converge on the unique canonical `Video`
  and shared content stages. Distinct command rows each retain their own reply
  intent.

## Diagnostics

- Add an internal-bearer-protected diagnostic list/detail endpoint only if
  needed for safe operation.
- Diagnostics may expose request ID, status, video ID, attempts, timestamps, and
  sanitized errors.
- Diagnostics must not expose raw bot/bearer tokens, configured identity values,
  raw command text beyond a safe normalized URL, stack traces, or provider
  response bodies.
- `/status` changes are optional and must be explicitly bounded if included.

## Non-Goals / Out Of Scope

- Telegram `getUpdates`, listener scripts, systemd installation, or webhook
  configuration.
- Multiple users/chats, user-managed allowlists, or destination fan-out.
- Reopening terminal pipeline stages or regenerating existing summaries.
- Email, mobile push, Activity DTO, subscription, monitoring, or marker changes.
- Celery, Redis, a broker, or a Telegram SDK.

## Files To Create Or Modify

- `app/services/on_demand_video.py` or equivalent 12A service boundary.
- `app/services/telegram_command_queue.py` or equivalent queue orchestrator.
- `app/services/transcript.py` for typed permanent-unavailable versus retryable
  failure outcomes required by the existing retry specification.
- `app/services/pipeline.py` for bounded content-only reuse/refactor.
- `app/services/telegram.py` for destination-aware typed replies.
- `app/api/routes/telegram_commands.py` for `process-next` and optional safe
  diagnostics.
- `app/services/polling.py` only if shared canonicalization/Short helpers must be
  extracted without behavior changes.
- `app/main.py` and polling route/service entry points as required to apply the
  shared PostgreSQL execution lock to startup, poll, and reconciliation work.
- Focused command-processing, pipeline-regression, Telegram, concurrency, and
  retry tests.

## Tests And Verification

- [ ] Queue tests prove oldest-due selection, atomic claim, no double claim,
      attempt transitions, due-time filtering, and stale-lease recovery.
- [ ] Worker API tests prove internal-only auth, empty queue behavior, one
      request per call, busy conflict, and sanitized outcomes.
- [ ] Metadata tests prove quota accounting and canonical upsert without
      `UserChannel`, email, push, or marker changes.
- [ ] Cached-summary tests prove no transcript or inference call and one reply
      for each distinct request.
- [ ] Shared-content tests prove transcript then summary behavior without an
      automatic Telegram stage.
- [ ] Existing automatic pipeline tests prove normal Telegram stages and
      fallback behavior remain unchanged.
- [ ] Interleaving tests prove poll/startup drains ignore manual-only stages and
      do not create unsolicited automatic Telegram/fallback stages.
- [ ] Shorts tests prove disabled classified Shorts make no content call and
      produce the policy response.
- [ ] Retry tests cover metadata transport, transcript failure, summary circuit,
      Google reauth pause, quota pause, Telegram timeout/429/5xx, permanent 4xx,
      and exhausted attempts.
- [ ] Transcript tests distinguish permanent unavailable/zero-snippet outcomes
      from retryable transport/provider errors for both manual and existing
      automatic pipeline paths.
- [ ] Delivery retry tests prove a generated summary is reused rather than
      regenerated.
- [ ] Reply tests verify authorized destination, `reply_parameters`, safe
      truncation, provider message ID persistence, and no token exposure.
- [ ] Concurrency tests prove the same request is not processed twice and two
      requests for one video share canonical content.
- [ ] Cross-process lock/fencing tests prove an expired timestamp cannot be
      reclaimed while the prior advisory-lock owner is active, a new lease token
      fences stale completion, sibling commands do not accelerate shared stage
      attempts, and startup/poll/command work cannot infer concurrently.
- [ ] Full regression suite and `git diff --check` pass.

Suggested verification:

```bash
.venv/bin/pytest tests/test_telegram_command_processing.py
.venv/bin/pytest tests/test_pipeline.py tests/test_polling_core.py
.venv/bin/pytest
git diff --check
```

## Acceptance Criteria

- [ ] One worker call durably claims and processes no more than one request.
- [ ] Stale claims recover without duplicate active processing.
- [ ] Manual commands reuse canonical transcript/summary state and never misuse
      the automatic per-video Telegram stage.
- [ ] Repeated distinct commands resend cached summaries without transcript or
      inference work.
- [ ] Request-specific replies target only the authorized chat and reply to the
      original message when supported.
- [ ] Quota, Shorts, Google reauth, summary circuit, retry, and terminal failure
      contracts are enforced.
- [ ] Expensive content work is not repeated because Telegram delivery failed.
- [ ] No email, push, subscription, monitoring, Activity, or marker side effect
      is introduced.
- [ ] Existing poll/reconciliation/startup/automatic Telegram behavior remains
      covered and unchanged except for the explicit transcript-outcome
      correction required to align existing code with the authoritative retry
      specification.
- [ ] Focused and full regression tests pass.

## Handoff To Phase 12C

- Phase 12C may rely only on the reviewed intake and `process-next` API
  contracts; it must not import ORM models or pipeline services into the
  listener.
- The feature remains disabled until the listener, systemd, operator rollout,
  and end-to-end verification in 12C are complete.
