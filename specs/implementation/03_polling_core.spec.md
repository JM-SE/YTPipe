# Polling Core Specification

## Context
This phase implements the MVP polling cycle for new upload detection. Authoritative references: `specs/polling_flow_definition.spec.md`, `specs/architecture_snapshot.spec.md`, and `specs/data_model_draft.spec.md`.

## Requirements
- [ ] Implement protected `POST /internal/run-poll` using bearer-secret authentication.
- [ ] Apply the quota safety gate before channel polling begins.
- [ ] Process monitored channels sequentially only.
- [ ] Detect latest uploads through each channel's uploads playlist.
- [ ] Update `SyncState` and per-channel baseline/current state during polling.
- [ ] Support partial-success behavior when one or more channels fail but useful processing still occurs.
- [ ] In scope: poll endpoint, quota gate, sequential detection, baseline/no-op/new-video branching, run outcome recording.
- [ ] Out of scope: email provider integration details, retry classification logic beyond creating the poll contract for later delivery handling.

## Technical Approach
Implement the exact poll order defined in `specs/polling_flow_definition.spec.md`: authenticate request, check quota budget, load monitored channels, process sequentially, detect latest upload via uploads playlist, establish baseline if missing, persist canonical video records when a new upload appears, and record aggregate run status. Channel-level failures must be recorded and must not abort the entire run.

## Implementation Steps
1. Add `POST /internal/run-poll` with `Authorization: Bearer <secret>` protection.
2. Check quota budget and safety stop before any channel work; return a blocked run outcome when polling is not allowed.
3. Load monitored `UserChannel` records and process them one at a time.
4. For each channel, resolve the latest upload from the uploads playlist and branch into baseline, unchanged, or new-video handling.
5. Persist `Video` records and update `UserChannel` and `SyncState` records to reflect channel and run outcomes, including partial success.
6. End the phase with a local testing handoff that gives step-by-step polling test instructions, a short manual checklist, and a clear split between what is locally testable and any missing external prerequisite.

## Acceptance Criteria
- [ ] Requests without the correct bearer secret cannot execute the polling flow.
- [ ] A quota-blocked run exits before channel processing begins.
- [ ] Channels are processed sequentially, not batched or parallelized.
- [ ] Channels with no baseline store the current latest upload and do not trigger notification work.
- [ ] A run with both successful and failed channel processing is recorded as `partial_success` rather than aborting outright.
- [ ] Phase completion includes a required local testing handoff with step-by-step instructions and a short manual checklist for the poll flow, or a precise statement of any missing prerequisite.
