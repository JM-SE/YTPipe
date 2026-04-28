# Future Improvements Specification

## Context

This document is the consolidated parking lot for explicitly deferred YTPipe work. Items listed here are not approved MVP implementation scope and do not grant permission to implement them without explicit approval and a dedicated spec update.

## Requirements

- [ ] Track deferred product, architecture, operations, and deployment improvements outside the active MVP phase specs.
- [ ] Keep the MVP scope protected from accidental expansion.
- [ ] Require explicit approval and spec updates before any listed item becomes implementation work.

## Technical Approach

Deferred items:

- Real Resend account, domain, sender verification, and a real-send smoke test before deployment readiness.
- Transactional outbox or equivalent design to avoid duplicate emails when provider send succeeds but the database commit fails.
- Poll-level lock or concurrency guard for overlapping `/internal/run-poll` calls.
- Race-safe get-or-create behavior and integrity-error recovery around unique `Video` and `NotificationDelivery` inserts.
- APScheduler or always-on in-app scheduler as a future alternative to the current external QStash scheduler.
- Homelab deployment as a future deployment evolution.
- UI/dashboard for channel management and operational status as future product evolution.
- Multi-channel notifications beyond email as future product evolution.
- Multi-user support as future product evolution.
- Celery, Redis, or background workers only as future explicitly approved architecture, not MVP infrastructure.
- Stronger token protection and encryption-at-rest for OAuth credentials.
- Richer observability and alerting beyond `/status`.
- QStash signature verification as future request-auth hardening; current MVP deployment relies on the existing internal bearer token forwarded by QStash.
- Broader database resilience and observability beyond the approved MVP `pool_pre_ping`/connection recycle follow-up in `specs/implementation/06b_database_connection_resilience.spec.md`.

## Implementation Steps

1. Do not implement items from this document during MVP phases unless a later approved phase spec explicitly moves that item into scope.
2. When an item is approved, create or update the relevant phase spec with requirements, technical approach, implementation steps, and acceptance criteria.
3. Keep this document current by removing or marking items only after they are covered by an approved implementation spec.

## Acceptance Criteria

- [ ] Deferred items are visible in one consolidated spec.
- [ ] The document states that listed items are not implementation permission.
- [ ] Future implementation requires explicit approval and a dedicated spec update.
