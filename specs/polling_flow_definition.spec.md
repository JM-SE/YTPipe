# Polling Flow Definition Specification

## Context
Step-by-step MVP execution flow for `POST /internal/run-poll`.

Normal polling is latest-upload-only. Historical reconciliation after an outage is governed separately by `specs/incident_recovery_and_reconciliation.spec.md` and must not be folded into the normal polling path.

## Requirements
- [ ] Define bearer protection, quota gating, lightweight quota-state persistence, sequential processing, baseline behavior, detection behavior, failure handling, retry pickup, aggregate response shape, and final outcome semantics.

## Technical Approach
1. Accept only `POST /internal/run-poll` requests with `Authorization: Bearer <secret>`.
2. Check the internal quota budget and safety stop before any polling work; persist lightweight quota metadata in `SyncState` process type `quota` for configured daily budget, estimated units used today, last run estimated units, and whether the safety stop is active or triggered. If blocked, end the run without channel processing.
3. Load only explicitly monitored channels for the single user.
4. Process channels sequentially in MVP.
5. For each channel, read the latest upload from the channel uploads playlist.
6. If a monitored channel has no baseline yet, store the current latest visible video as baseline, set monitoring-state fields, and do not notify.
7. If the latest upload matches stored state, do nothing for that channel.
8. If a new video is detected after baseline, persist the canonical `Video` record, create or confirm the `NotificationDelivery` record, attempt the email send, and update channel state.
9. During the same run, pick up any deliveries already marked `pending_retry` and attempt their one allowed retry.
10. If a monitored channel has no usable uploads playlist, including a `playlistNotFound`-style result, classify it as channel-level `unusable_uploads_playlist`, surface that classification in operational state/status, and continue with remaining channels without auto-disabling the channel.
11. If a channel fails during detection or processing for any other reason, record the channel error and continue with remaining channels.
12. Complete the run with outcome semantics: `success` when no channel errors occur, `partial_success` when at least one channel succeeds and at least one channel fails, and `failed` when the run cannot perform useful channel processing.
13. Return only an aggregate summary payload with concise fields such as run outcome, processed count, failed count, baselines established, new videos detected, and quota-blocked indicator; do not return per-channel detail from the poll endpoint.

## Implementation Steps
1. Treat quota gating as the first execution guard.
2. Do not assume subscription import already established baseline; keep baseline establishment inside the polling contract for monitored channels.
3. Record aggregate run status, quota state, and per-channel errors for `/status` visibility.

## Acceptance Criteria
- [ ] The endpoint contract includes bearer protection.
- [ ] Quota metadata is persisted lightly in `SyncState` process type `quota` for operational visibility.
- [ ] Channel processing is explicitly sequential.
- [ ] Polling loads only explicitly monitored channels.
- [ ] Baseline establishment does not create notifications.
- [ ] Missing or unusable uploads playlists are classified as `unusable_uploads_playlist`, remain visible operationally, and do not auto-disable the channel.
- [ ] Channel-level failures do not abort the full run.
- [ ] The poll endpoint response is aggregate-only and excludes per-channel detail.
- [ ] Final run outcomes include success, partial success, and failed semantics.
