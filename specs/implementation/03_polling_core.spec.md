# Polling Core Specification

## Context
This phase implements the MVP polling cycle for new upload detection. Authoritative references: `specs/polling_flow_definition.spec.md`, `specs/architecture_snapshot.spec.md`, and `specs/data_model_draft.spec.md`.

## Requirements
- [ ] Implement protected `POST /internal/run-poll` using bearer-secret authentication.
- [ ] Apply the quota safety gate before channel polling begins.
- [ ] Persist lightweight quota-visibility state in `SyncState` process type `quota` during poll execution.
- [ ] Process only explicitly monitored channels sequentially.
- [ ] Detect latest uploads through each channel's uploads playlist.
- [ ] Update `SyncState` and per-channel baseline/current state during polling.
- [ ] Classify missing or unusable uploads playlists as channel-level errors without aborting the run or auto-disabling the channel.
- [ ] Return only an aggregate poll summary payload, not per-channel detail.
- [ ] Support partial-success behavior when one or more channels fail but useful processing still occurs.
- [ ] In scope: poll endpoint, quota gate, lightweight quota state, sequential detection, baseline/no-op/new-video branching, channel error classification, aggregate run summary, run outcome recording.
- [ ] Out of scope: email provider integration details, retry classification logic beyond creating the poll contract for later delivery handling.

## Technical Approach
Implement the exact poll order defined in `specs/polling_flow_definition.spec.md`: authenticate request, check quota budget, update lightweight quota visibility in `SyncState` process type `quota`, load only explicitly monitored channels, process sequentially, detect latest upload via uploads playlist, establish baseline if missing for monitored channels, persist canonical video records when a new upload appears, and record aggregate run status. `POST /internal/run-poll` returns only an aggregate summary payload. Do not assume subscription import already populated baseline state. Channel-level failures, including `unusable_uploads_playlist`, must be recorded and must not abort the entire run or auto-disable the channel.

## Implementation Steps
1. Add `POST /internal/run-poll` with `Authorization: Bearer <secret>` protection.
2. Check quota budget and safety stop before any channel work; persist lightweight quota metadata in `SyncState` process type `quota` and return a blocked aggregate run outcome when polling is not allowed.
3. Load only monitored `UserChannel` records and process them one at a time.
4. For each channel, resolve the latest upload from the uploads playlist and branch into baseline, unchanged, new-video, or channel-error handling; classify missing/unusable playlist cases as `unusable_uploads_playlist`.
5. Persist `Video` records and update `UserChannel` and `SyncState` records to reflect channel and run outcomes, including partial success, without auto-disabling errored channels.
6. Return an aggregate-only response containing concise outcome fields such as run outcome, processed count, failed count, baselines established, new videos detected, and quota-blocked indicator.
7. End the phase with a local testing handoff that gives step-by-step polling test instructions, a short manual checklist, and a clear split between what is locally testable and any missing external prerequisite.

## Acceptance Criteria
- [ ] Requests without the correct bearer secret cannot execute the polling flow.
- [ ] A quota-blocked run exits before channel processing begins.
- [ ] Quota visibility for MVP is persisted in `SyncState` process type `quota` with lightweight metadata for configured daily budget, estimated units used today, last run estimated units, and safety-stop state.
- [ ] Channels are processed sequentially, not batched or parallelized.
- [ ] Only explicitly monitored channels are loaded for polling.
- [ ] Monitored channels with no baseline store the current latest upload and do not trigger notification work.
- [ ] A monitored channel with no usable uploads playlist is recorded as a channel-level `unusable_uploads_playlist` error, remains monitored, and does not abort the rest of the run.
- [ ] `POST /internal/run-poll` returns only an aggregate summary payload with outcome-oriented fields and no per-channel detail.
- [ ] A run with both successful and failed channel processing is recorded as `partial_success` rather than aborting outright.
- [ ] Phase completion includes a required local testing handoff with step-by-step instructions and a short manual checklist for the poll flow, or a precise statement of any missing prerequisite.
