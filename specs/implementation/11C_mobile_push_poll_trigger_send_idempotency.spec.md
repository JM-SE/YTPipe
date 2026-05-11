# Backend Phase 11C Mobile Push Poll Trigger, Send, Idempotency Specification

## Context

Phase 11C integrates mobile push delivery into `YouTubePollingService.run_poll` for newly detected YouTube uploads. It builds on Phase 11A/11B mobile-push schema, settings, delivery ledger, preference helpers, installation lookup, payload builders, and Expo test-send behavior in `app/services/mobile_push.py`.

Current polling already creates a canonical `Video`, creates or reuses the email/activity `NotificationDelivery`, attempts initial email delivery, then updates `UserChannel.last_seen_video_id` and poll counters in the new-video branch. Pending/retry email delivery processing occurs before channel polling and must not trigger push.

## Requirements

- [ ] Integrate push only inside `YouTubePollingService.run_poll` new-video branch after canonical `Video` and email/activity `NotificationDelivery` exist.
- [ ] Manual mobile run-poll and QStash/internal automatic poll trigger push identically because both use `run_poll`.
- [ ] Push must not trigger for baseline establishment, unchanged latest upload, unmonitored channels, pending initial email processing, retry email processing, quota/safety block, sync failure, poll failure, or channel polling failure.
- [ ] Push does not depend on email provider success: once the new-video branch has `Video` and `NotificationDelivery`, email failure must not suppress push.
- [ ] Push failure must not alter email delivery status, fail the poll, roll back polling changes, or prevent `last_seen_video_id` updates.
- [ ] Enforce global app/settings gates before fan-out: `PUSH_NOTIFICATIONS_ENABLED=true`, `mobile_push_settings.enabled=true`, monitored `UserChannel`, and effective channel preference enabled via `compute_channel_push_state`.
- [ ] If app-level global push settings or runtime provider sending is disabled, do not fan out and do not create new `mobile_push_deliveries` rows.
- [ ] Fan out synchronously, best-effort, to every eligible owner installation that is enabled, registered, not invalidated/unregistered, and has a token.
- [ ] Use `get_or_create_new_video_delivery(...)` for idempotency by `(notification_delivery_id, installation_id)`.
- [ ] Do not duplicate sends for existing `sent` rows.
- [ ] MVP retry semantics: only attempt newly created or existing `pending` rows with `attempt_count == 0`; do not retry existing `failed`, `invalid_token`, `skipped`, or already-attempted rows on later polls.
- [ ] Record attempt count, attempt timestamps, success/failure, Expo ticket/status, sanitized error/response, and installation summary fields consistently with 11B test sends.
- [ ] Invalid Expo token responses mark/disable/invalidates the installation and record `invalid_token`.
- [ ] Payload `data` includes only safe fields: `type: "new_video"`, `activity_id`, `delivery_id`, `video_id`, `channel_id`, and `sent_at`.
- [ ] No raw Expo token, bearer/internal/provider secrets, internal URLs, stack traces, or sensitive diagnostics may appear in payload data, responses, persisted sanitized fields, or logs.
- [ ] Preserve existing Activity behavior: push tap opens Activity and refetches `/internal/activity`; do not modify Activity DTOs.

## Non-Goals / Out Of Scope

- [ ] No new endpoints, endpoint auth changes, or mobile-push endpoint contract changes.
- [ ] No UI or mobile app work.
- [ ] No receipt polling.
- [ ] No background jobs, Celery, Redis, queues, outbox worker, or provider SDK requirement.
- [ ] No changes to `/internal/channels`, `/internal/activity`, `/status`, or mobile-push endpoint DTOs.
- [ ] No public run-poll response contract changes unless an unavoidable implementation issue is explicitly documented.
- [ ] No push for failures or operational events other than new-video detection.

## Technical Approach

Reuse/extract the Expo sender, response parsing, sanitization, and invalid-token handling from the 11B `/internal/mobile-push/test` path so test sends and new-video sends share one safe provider implementation. Avoid duplicating raw `httpx` provider logic in polling.

Add a `MobilePushService.attempt_new_video_push(...)`-style method that receives the active session, owner user, `UserChannel`/`Channel`, `Video`, and `NotificationDelivery`. The method should perform all push gates, eligible-installation selection, idempotent delivery-row creation/lookup, send attempts, status persistence, and per-delivery exception handling.

Integrate `MobilePushService` into `YouTubePollingService` minimally. The implementation may use an optional constructor dependency, a service factory, or route-level construction, as long as existing tests remain stable and `run_poll` remains the single trigger path.

Recommended call point: after `_get_or_create_delivery(...)` and `_attempt_initial_delivery_send(...)`, before `last_seen_video_id` is updated. Email success is not required; push exceptions must be swallowed/recorded so `last_seen_video_id`, `new_videos_detected`, and `channels_processed` still update according to existing poll behavior.

## Files To Create Or Modify

- `app/services/mobile_push.py` — extract/reuse generic Expo send helper and add new-video push orchestration.
- `app/services/polling.py` — call the new mobile-push service only in the new-video branch.
- Tests, likely `tests/test_mobile_push_polling.py` plus focused additions to existing polling/mobile-push tests.

No endpoint, DTO, route-auth, Activity, channel catalog, status, background worker, or run-poll response contract changes should be needed.

## Implementation Steps

1. Review the 11B test-send implementation and extract a generic Expo send helper used by both test and new-video sends.
2. Add a new-video payload builder/use existing builder with safe `data` fields only.
3. Add `MobilePushService.attempt_new_video_push(...)` or equivalent with gates for runtime provider enabled, global setting enabled, monitored channel, effective channel preference, and eligible installations.
4. For each eligible installation, call `get_or_create_new_video_delivery(...)`; send only if the row is newly created or is `pending` with `attempt_count == 0`.
5. Persist success/failure/invalid-token status, sanitized Expo fields, `attempt_count`, timestamps, and installation summary fields.
6. Ensure all provider and push-service exceptions are caught per delivery or at the push-service boundary and never bubble into polling channel failure handling.
7. Wire `MobilePushService` into `YouTubePollingService` using a minimally invasive dependency pattern that preserves existing tests.
8. Call new-video push from `run_poll` only after `Video` and `NotificationDelivery` exist, preferably after initial email attempt and before `last_seen_video_id` update.
9. Add required tests and regression checks.

## Testing And Verification

Required tests:

- [ ] New-video branch with runtime enabled, global settings enabled, effective channel enabled, and one eligible installation sends exactly once.
- [ ] Sent new-video push creates `mobile_push_deliveries` with `event_type='new_video'`, `status='sent'`, references to `notification_delivery`, `video`, and `channel`, and safe payload data.
- [ ] Multiple eligible installations fan out one delivery per installation.
- [ ] Idempotency: repeated run/new branch or pre-existing `sent` delivery does not send duplicate for the same `(notification_delivery_id, installation_id)`.
- [ ] Global app setting disabled creates no fan-out rows and makes no provider call.
- [ ] `PUSH_NOTIFICATIONS_ENABLED=false` creates no fan-out rows, makes no provider network call, and does not fail poll.
- [ ] Channel explicit disabled/effective disabled makes no provider call.
- [ ] Unregistered, disabled, invalidated, or tokenless installation makes no provider call.
- [ ] Baseline establishment creates no push delivery and sends no push.
- [ ] Unchanged latest upload creates no push delivery and sends no push.
- [ ] Pending initial/retry email processing before channel polling sends no push.
- [ ] Email delivery failure in the new-video branch does not suppress push.
- [ ] Push failure does not alter email delivery status.
- [ ] Provider HTTP failure, malformed response, timeout, or exception is recorded as `failed` and does not fail poll.
- [ ] Invalid token marks/disables/invalidates installation and records `invalid_token`.
- [ ] Quota/safety block sends no push.
- [ ] Run-poll API still returns the existing shape and succeeds/partial-succeeds independently of push failures.
- [ ] Regression/grep checks prove no route contract changes and no mobile-push endpoint auth changes.

Suggested verification commands for `@tech-lead`:

```powershell
python -m pytest tests/test_mobile_push_polling.py
python -m pytest tests/test_polling_core.py tests/test_mobile_push_api.py tests/test_mobile_push_polling.py
python -m pytest
```

Use repository-specific test names if implementation chooses a different test file organization.

## Acceptance Criteria

- [ ] Push is integrated only through `YouTubePollingService.run_poll` new-video branch after canonical `Video` and `NotificationDelivery` exist.
- [ ] Manual and automatic/QStash polls trigger identically through `run_poll`.
- [ ] Baseline, unchanged latest video, unmonitored channels, email retry processing, quota/safety block, sync failure, poll failure, and channel polling failure never trigger push.
- [ ] Email provider failure does not suppress new-video push, and push failure does not alter email delivery status.
- [ ] Disabled runtime provider flag or disabled app-level push settings produce no fan-out rows and no provider call.
- [ ] Effective channel preference and installation eligibility gates are enforced.
- [ ] New-video push fan-out is idempotent by `(notification_delivery_id, installation_id)` and does not duplicate sent or previously attempted terminal rows.
- [ ] Success, provider failure, malformed response, timeout, exception, and invalid-token outcomes are persisted with sanitized fields.
- [ ] Invalid Expo token handling disables/marks the installation.
- [ ] Push failures are caught and recorded without changing poll summary outcome or rolling back polling state.
- [ ] Existing `/internal/channels`, `/internal/activity`, `/status`, run-poll response shape, and mobile-push endpoint contracts remain unchanged unless explicitly documented as unavoidable.
- [ ] No endpoints, UI, background workers, receipt polling, Celery, Redis, or Expo SDK dependency are added.
- [ ] No raw tokens, secrets, provider credentials, internal URLs, stack traces, or sensitive diagnostics appear in payload data, responses, persisted sanitized fields, or logs.
- [ ] Required Phase 11C tests and full regression suite pass.

## Handoff Notes / Next Steps

- Implement through `@tech-lead` only, one phase at a time, following the repository phase index and review gates.
- Keep the change tightly scoped to polling integration and shared mobile-push service behavior.
- If implementing 11C reveals a contract gap requiring endpoint, DTO, or poll response changes, stop and update specs before coding beyond this phase.
