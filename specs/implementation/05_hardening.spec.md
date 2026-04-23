# Hardening Specification

## Context
This phase makes the implemented MVP safe to run unattended within the approved scope. Authoritative references: `specs/retry_and_deduplication_rules.spec.md`, `specs/data_model_draft.spec.md`, `specs/polling_flow_definition.spec.md`, and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Enforce deduplication constraints and idempotent write behavior across detection and delivery flows.
- [ ] Ensure transactional behavior for state updates that must stay consistent.
- [ ] Record robust channel, run, and delivery errors for operational visibility.
- [ ] Define the full `/status` response contract for MVP observability.
- [ ] Validate partial-failure behavior so useful work is preserved when individual channels or deliveries fail.
- [ ] In scope: DB safety, error recording, `/status` contract, observability fields, behavior validation around partial failures.
- [ ] Out of scope: new product features, background workers, UI dashboards.

## Technical Approach
Use database constraints from `specs/data_model_draft.spec.md` and retry/idempotency rules from `specs/retry_and_deduplication_rules.spec.md` as the enforcement baseline. Treat video creation, delivery creation, and related state transitions as idempotent operations guarded by uniqueness constraints and transactional boundaries. Expand `/status` to expose the MVP observability set already approved in `specs/youtube_notifier_specs_v_2.spec.md`.

## Implementation Steps
1. Validate all uniqueness constraints and upsert-or-confirm patterns used by polling and notification flows.
2. Add transactional boundaries so partially completed writes do not create inconsistent notification state.
3. Persist structured error details for subscription sync, polling runs, channel failures, quota blocks, and delivery failures.
4. Implement the approved `/status` contract for sync, polling, email, quota, and monitored-channel visibility.
5. Verify that partial-failure scenarios preserve successful work and surface accurate aggregate status.
6. End the phase with a local testing handoff that explains how to verify deduplication, error visibility, and `/status` locally, plus a short manual checklist and any stated gap for non-local verification.

## Acceptance Criteria
- [ ] Re-detecting the same YouTube video cannot create duplicate canonical video rows or duplicate user delivery rows.
- [ ] Polling and delivery writes are idempotent enough to tolerate safe re-entry after recoverable failures.
- [ ] `/status` exposes last sync result, polling result, delivery result, quota state, and monitored channel count.
- [ ] Channel or email failures are recorded with enough detail to diagnose the failing step.
- [ ] Partial-failure runs preserve successful channel outcomes while clearly reporting failed ones.
- [ ] Phase completion includes a required local testing handoff with step-by-step instructions and a short manual checklist for the locally verifiable hardening behavior.
