# Hardening Specification

## Context
This phase makes the implemented MVP safe to run unattended within the approved scope. Authoritative references: `specs/retry_and_deduplication_rules.spec.md`, `specs/data_model_draft.spec.md`, `specs/polling_flow_definition.spec.md`, `specs/youtube_notifier_specs_v_2.spec.md`, and `specs/future_improvements.spec.md`.

## Requirements
- [ ] Enforce deduplication constraints and idempotent write behavior across detection and delivery flows.
- [ ] Ensure transactional behavior for state updates that must stay consistent.
- [ ] Require a hardening-phase schema smoke test that verifies the expected core tables and key uniqueness constraints remain present at the DB level.
- [ ] Record robust channel, run, and delivery errors for operational visibility.
- [ ] Define the full `/status` response contract for MVP observability.
- [ ] Protect `/status` with the same internal bearer token contract used by other internal endpoints; `/health` remains the public uptime endpoint.
- [ ] Keep email/delivery failures separate from channel polling failures: successful detection with email failure must not increment `channels_failed`.
- [ ] Document the accepted MVP transactional limitation that email is sent before the final database commit, creating a possible duplicate-send risk if commit fails after provider success.
- [ ] Validate partial-failure behavior so useful work is preserved when individual channels or deliveries fail.
- [ ] In scope: DB safety, error recording, `/status` contract, observability fields, behavior validation around partial failures.
- [ ] Out of scope: new product features, background workers, UI dashboards.

## Technical Approach
Use database constraints from `specs/data_model_draft.spec.md` and retry/idempotency rules from `specs/retry_and_deduplication_rules.spec.md` as the enforcement baseline. Treat video creation, delivery creation, and related state transitions as idempotent operations guarded by uniqueness constraints and transactional boundaries where practical for MVP. The known send-before-final-commit duplicate risk is accepted for MVP and tracked as future work in `specs/future_improvements.spec.md`.

`/status` is an internal operational endpoint and must require the same `Authorization: Bearer <secret>` protection used by other internal endpoints. `/health` remains the public, unauthenticated uptime endpoint.

The concise `/status` response contract is:

- `service`: service name.
- `environment`: current application environment.
- `ready`: boolean readiness summary.
- `subscription_sync`: `last_success_at`, `last_error_at`, `last_error_message`, `metadata`.
- `polling`: `last_success_at`, `last_error_at`, `last_error_message`, `last_run` from `SyncState` metadata containing `run_outcome`, `channels_processed`, `channels_failed`, `baselines_established`, `new_videos_detected`, `quota_blocked`, and `channel_errors` if stored.
- `email`: `last_attempt_at`, `last_success_at`, `last_failure_at`, `last_error`, `pending_count`, `pending_retry_count`, `delivered_count`, `failed_count`.
- `quota`: `daily_quota_budget`, `estimated_units_used_today`, `last_run_estimated_units`, `safety_stop_active`, `safety_stop_enabled`, and `safety_stop_triggered_at` if available.
- `channels`: `imported_count`, `monitored_count`.

Email and delivery failures affect delivery state and `/status.email`; they must not turn otherwise successful channel detection into `channels_failed`. Channel failures remain reserved for detection/channel-processing failures such as unusable uploads playlists or YouTube polling errors.

## Implementation Steps
1. Validate all uniqueness constraints and upsert-or-confirm patterns used by polling and notification flows.
2. Add automated pytest schema smoke coverage against the target test database to confirm the expected core tables still exist and that `Video.youtube_video_id` global uniqueness and `NotificationDelivery unique(user_id, video_id)` are enforced at the DB level.
3. Confirm transactional boundaries for state updates that can be made consistent in MVP, and document the accepted send-before-final-commit duplicate risk as future work rather than solving it in this phase.
4. Persist structured error details for subscription sync, polling runs, channel failures, quota blocks, and delivery failures.
5. Implement the protected `/status` contract for sync, polling, email, quota, and monitored-channel visibility.
6. Verify that partial-failure scenarios preserve successful work and surface accurate aggregate status, including email failures that do not increment `channels_failed` when detection succeeded.
7. End the phase with a local testing handoff that explains how to verify deduplication, schema smoke coverage, error visibility, and `/status` locally, plus a short manual checklist and any stated gap for non-local verification.

## Acceptance Criteria
- [ ] Re-detecting the same YouTube video cannot create duplicate canonical video rows or duplicate user delivery rows.
- [ ] Polling and delivery writes are idempotent enough to tolerate safe re-entry after recoverable failures.
- [ ] An automated pytest schema smoke test verifies the expected core tables remain present and confirms DB-level enforcement of `Video.youtube_video_id` global uniqueness and `NotificationDelivery unique(user_id, video_id)`.
- [ ] `/status` is protected by the internal bearer token contract; `/health` remains public for uptime checks.
- [ ] `/status` exposes top-level `service`, `environment`, and `ready`, plus `subscription_sync`, `polling`, `email`, `quota`, and `channels` sections matching the response contract above.
- [ ] Channel or email failures are recorded with enough detail to diagnose the failing step.
- [ ] Email/delivery failures are surfaced in delivery state and `/status.email`, and do not increment `channels_failed` when channel detection itself succeeded.
- [ ] Partial-failure runs preserve successful channel outcomes while clearly reporting failed ones.
- [ ] The accepted MVP send-before-final-commit duplicate risk is documented and linked to the future improvements spec.
- [ ] Phase completion includes a required local testing handoff with step-by-step instructions and a short manual checklist for the locally verifiable hardening behavior.
