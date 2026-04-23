# Implementation Phase Index Specification

## Goal
- Deliver the approved MVP in executable phases using `specs/architecture_snapshot.spec.md`, `specs/data_model_draft.spec.md`, `specs/polling_flow_definition.spec.md`, `specs/retry_and_deduplication_rules.spec.md`, and `specs/agent_guardrails.spec.md` as the source of truth.

## Constraints
- Single-user, no UI, email-only, polling-only MVP.
- Approved stack and hosting decisions are frozen by `specs/architecture_snapshot.spec.md`.
- Implementation must remain sequential: do not start a later phase until the current phase acceptance criteria are met.
- A phase is not ready for review until the implementation handoff includes local testing guidance and a manual checklist whenever local verification is possible.
- If a prerequisite phase is incomplete, implementation must stop at that boundary rather than skipping ahead.

## Phases
1. `01_foundations.spec.md` - Establish project skeleton, configuration contract, database connectivity, Alembic, base models, app startup, and a minimal health/status placeholder.
2. `02_auth_and_subscription_import.spec.md` - Add Google OAuth, token persistence, minimum-scope policy, token refresh contract, and initial subscription import with baseline-only monitoring records.
3. `03_polling_core.spec.md` - Implement protected polling entrypoint, quota safety gate, sequential channel polling, baseline/new-video handling, sync state updates, and partial-success run semantics.
4. `04_notifications.spec.md` - Add Resend delivery path, notification state transitions, and next-cycle retry pickup behavior.
5. `05_hardening.spec.md` - Enforce deduplication, idempotent and transactional behavior, error recording, `/status` contract, and observability coverage.
6. `06_deployment_readiness.spec.md` - Finalize environment contract, deployment assumptions, production-safe checks, smoke tests, and free-tier operating guidance.

## Dependencies
- Phase 1 is required before all other phases.
- Phase 2 depends on the Phase 1 app, DB, and migration baseline.
- Phase 3 depends on Phase 2 channel data, OAuth persistence, and subscription-import baseline records.
- Phase 4 depends on Phase 3 creating `Video` and `NotificationDelivery` records during poll processing.
- Phase 5 depends on Phases 3 and 4 so hardening validates the real poll and delivery flow rather than placeholders.
- Phase 6 depends on all prior phases meeting acceptance so deployment work reflects the actual implemented contract.

## Risks
- OAuth token handling can fail if refresh behavior is not treated as a strict persistence contract.
- YouTube quota usage can drift if quota gating is added after polling behavior instead of before it.
- Duplicate notifications can leak through if DB constraints and idempotent write paths are deferred.
- Free-tier hosting assumptions can break unattended operation if deployment checks and smoke tests are skipped.

## Acceptance Criteria
- [ ] The implementation plan is broken into sequential, executable phases.
- [ ] Each phase maps to an implementation spec in `specs/implementation/`.
- [ ] Dependencies between phases are explicit.
- [ ] The plan states that later phases must not begin before prerequisite acceptance is met.
- [ ] The plan states that phase review readiness includes a local testing handoff and manual checklist whenever local verification is possible.
