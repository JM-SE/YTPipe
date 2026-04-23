# Foundations Specification

## Context
This phase establishes the minimum backend foundation required for all later phases. Authoritative references: `specs/architecture_snapshot.spec.md` and `specs/data_model_draft.spec.md`.

## Requirements
- [ ] Create the project skeleton for the approved FastAPI, SQLAlchemy, Alembic, and PostgreSQL stack.
- [ ] Define configuration and settings loading for local and deployed environments.
- [ ] Establish database connectivity and Alembic migration setup.
- [ ] Create base ORM models for the approved domain entities without expanding product scope.
- [ ] Support app startup and expose a minimal health/status placeholder.
- [ ] In scope: skeleton, config contract, DB wiring, migrations, model definitions, startup wiring.
- [ ] Out of scope: OAuth logic, subscription import, polling behavior, email delivery, full `/status` observability.

## Technical Approach
Use the stack frozen in `specs/architecture_snapshot.spec.md`. Translate the seven approved entities from `specs/data_model_draft.spec.md` directly into initial ORM and migration artifacts, including the required unique constraints. Keep `/status` minimal in this phase: enough to prove the app starts and the service can report a placeholder operational response.

## Implementation Steps
1. Create the FastAPI application structure and shared settings module.
2. Wire PostgreSQL connection management and a DB session pattern suitable for later API and job flows.
3. Initialize Alembic and generate the first migration baseline for the approved schema.
4. Define the approved base models and timestamps with the required uniqueness constraints.
5. Add startup-ready routes for a simple health response and placeholder `/status` response.
6. End the phase with a local testing handoff that gives step-by-step setup and verification instructions plus a short manual checklist for app startup, DB migration, health, and placeholder `/status` behavior.

## Acceptance Criteria
- [ ] The app starts with configuration loaded from environment variables.
- [ ] A PostgreSQL connection path is defined and Alembic is initialized.
- [ ] The initial schema covers `User`, `OAuthAccount`, `Channel`, `UserChannel`, `Video`, `NotificationDelivery`, and `SyncState`.
- [ ] `Video.youtube_video_id` and `NotificationDelivery (user_id, video_id)` uniqueness are present in the schema plan.
- [ ] A basic health endpoint and placeholder `/status` endpoint respond without requiring later-phase features.
- [ ] Phase completion includes a local testing handoff with step-by-step instructions and a short manual checklist for the locally verifiable foundation behavior.
