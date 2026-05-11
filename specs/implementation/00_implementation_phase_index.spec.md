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
2. `02_auth_and_subscription_import.spec.md` - Add Google OAuth, token persistence, minimum-scope policy, token refresh contract, and subscription catalog sync without mass baseline establishment.
3. `02a_channel_monitoring_management.spec.md` - Add internal MVP endpoints to list imported channels and explicitly enable or disable monitoring per channel.
4. `03_polling_core.spec.md` - Implement protected polling entrypoint, quota safety gate, sequential polling of monitored channels only, baseline/new-video handling, sync state updates, and partial-success run semantics.
5. `04_notifications.spec.md` - Add Resend delivery path, notification state transitions, and next-cycle retry pickup behavior.
6. `05_hardening.spec.md` - Enforce deduplication, idempotent and transactional behavior, error recording, `/status` contract, and observability coverage.
7. `06_deployment_readiness.spec.md` - Finalize environment contract, deployment assumptions, production-safe checks, smoke tests, and free-tier operating guidance.

Post-staging appendix/follow-up: `06c_production_finalization.spec.md` - Finalize real Resend validation and production deployment readiness after staging has passed, without changing the completed MVP phase order.

Execution workflow/policy: `07_execution_workflow.spec.md` remains an execution workflow and repository policy spec, not a product functionality phase.

## Post-MVP Mobile-Readiness Phases

These phases prepare the backend API for a future React Native/Expo companion app without changing the completed MVP phase order.

1. `08_mobile_api_auth_and_swagger.spec.md` - Add separate mobile/admin bearer-token contract and improve Swagger/OpenAPI usability while preserving protected docs behavior.
2. `09_channel_catalog_api.spec.md` - Make the channel catalog endpoint UI-friendly with filters, pagination, default monitored view, and activation docs.
3. `10_mobile_activity_api.spec.md` - Add read-only mobile activity/history endpoint for detected videos and delivery status.

## Post-MVP Mobile Push Backend Phases

These phases add Expo-backed mobile push support after mobile-readiness APIs, without changing completed MVP phase history.

1. `11A_mobile_push_schema_settings_service.spec.md` - Add push settings, schema, models, and service skeleton without endpoints, polling integration, or real sends.
2. `11B_mobile_push_endpoints_status_preferences_test.spec.md` - Add mobile-push endpoints for status, registration, settings, channel preferences, unregister, and synchronous test sends.
3. `11C_mobile_push_poll_trigger_send_idempotency.spec.md` - Future phase for polling new-video trigger integration, fan-out, and idempotent push delivery behavior.

## Dependencies
- Phase 1 is required before all other phases.
- Phase 2 depends on the Phase 1 app, DB, and migration baseline.
- Phase 2a depends on Phase 2 catalog data and OAuth-backed channel records.
- Phase 3 depends on Phase 2a monitored-channel state and Phase 2 OAuth persistence.
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
