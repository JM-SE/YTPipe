# Polling Flow Definition Specification

## Context
Step-by-step MVP execution flow for `POST /internal/run-poll`.

## Requirements
- [ ] Define bearer protection, quota gating, sequential processing, baseline behavior, detection behavior, failure handling, retry pickup, and final outcome semantics.

## Technical Approach
1. Accept only `POST /internal/run-poll` requests with `Authorization: Bearer <secret>`.
2. Check the internal quota budget and safety stop before any polling work; if blocked, end the run without channel processing.
3. Load monitored channels for the single user.
4. Process channels sequentially in MVP.
5. For each channel, read the latest upload from the channel uploads playlist.
6. If no baseline exists for that channel, store the current latest visible video as baseline and do not notify.
7. If the latest upload matches stored state, do nothing for that channel.
8. If a new video is detected after baseline, persist the canonical `Video` record, create or confirm the `NotificationDelivery` record, attempt the email send, and update channel state.
9. During the same run, pick up any deliveries already marked `pending_retry` and attempt their one allowed retry.
10. If a channel fails during detection or processing, record the channel error and continue with remaining channels.
11. Complete the run with outcome semantics: `success` when no channel errors occur, `partial_success` when at least one channel succeeds and at least one channel fails, and `failed` when the run cannot perform useful channel processing.

## Implementation Steps
1. Treat quota gating as the first execution guard.
2. Keep channel detection and retry pickup inside one polling cycle contract.
3. Record aggregate run status and per-channel errors for `/status` visibility.

## Acceptance Criteria
- [ ] The endpoint contract includes bearer protection.
- [ ] Channel processing is explicitly sequential.
- [ ] Baseline establishment does not create notifications.
- [ ] Channel-level failures do not abort the full run.
- [ ] Final run outcomes include success, partial success, and failed semantics.
