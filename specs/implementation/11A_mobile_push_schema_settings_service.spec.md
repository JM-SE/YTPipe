# Backend Phase 11A Mobile Push Schema, Settings, Service Skeleton Specification

## Context

This phase prepares backend storage, configuration, and service contracts for mobile push notifications using `specs/mobile_push_notifications_backend_cross_repo.spec.md` as the source of truth. The backend is FastAPI + SQLAlchemy + Alembic + Pydantic settings on Python 3.13. Existing models include `Channel`, `UserChannel`, `Video`, `NotificationDelivery`, and `SyncState`.

Phase 11A is schema/settings/service-skeleton only. It must not add API routes, must not wire polling triggers, and must not perform real Expo sends. Later phases use this foundation:

- Phase 11B: endpoints, status, preferences, registration/unregister, and test API behavior.
- Phase 11C: polling trigger, synchronous best-effort Expo send, and delivery idempotency integration.

## Requirements

- [ ] Add push settings/env support in `app/core/settings.py`, `.env.example`, and `render.yaml` with safe defaults.
- [ ] Add SQLAlchemy models and Alembic migration for `mobile_push_settings`, `mobile_push_installations`, `mobile_push_channel_preferences`, and `mobile_push_deliveries`.
- [ ] Preserve existing mobile API contracts and avoid changes to `/internal/channels`, `/internal/activity`, and `/status` DTOs.
- [ ] Include the new Alembic migration in `pyproject.toml` data-files/package data if the repository pattern requires it.
- [ ] Add `app/services/mobile_push.py` service skeleton with future-safe interfaces for Phase 11B and Phase 11C.
- [ ] Make service behavior safe when `PUSH_NOTIFICATIONS_ENABLED=false`.
- [ ] Add tests for settings defaults, schema/migration smoke coverage, and service skeleton behavior.
- [ ] Ensure the existing test suite still passes.

## Non-Goals / Out Of Scope

- [ ] Do not add mobile-push API routes or include a router in `app/main.py`.
- [ ] Do not add 11B endpoint DTOs, status endpoint behavior, preference endpoint behavior, or test endpoint behavior beyond reusable service skeleton contracts.
- [ ] Do not wire mobile push into polling or `YouTubePollingService.run_poll`.
- [ ] Do not perform real Expo HTTP sends in this phase.
- [ ] Do not add Celery, Redis, background workers, queues, or new provider SDK dependencies.
- [ ] Do not change existing `/internal/channels`, `/internal/activity`, `/status`, or mobile auth contracts.
- [ ] Do not implement auth behavior; note only that Phase 11B endpoints must use `require_mobile_bearer_token`.

## Technical Approach

Add final MVP push tables and ORM models now so later endpoint and polling phases can build against stable storage. Add settings with disabled-by-default runtime flags and Expo endpoint defaults. Add a service skeleton that centralizes global settings creation, token masking, effective channel preference evaluation, installation registration/unregistration helpers, delivery ledger idempotency helpers, and payload construction without provider sends.

The service should be importable and unit-testable without requiring network access. Any future sender abstraction must no-op or return a disabled/skipped result when `PUSH_NOTIFICATIONS_ENABLED=false`.

## Files To Create Or Modify

- `app/core/settings.py` — add push-related Pydantic settings.
- `.env.example` — document push env vars with safe defaults.
- `render.yaml` — add push env vars safely; use defaults for flags/endpoints and `sync: false` only if `EXPO_ACCESS_TOKEN` is included.
- `app/models/mobile_push_setting.py` — suggested model file.
- `app/models/mobile_push_installation.py` — suggested model file.
- `app/models/mobile_push_channel_preference.py` — suggested model file.
- `app/models/mobile_push_delivery.py` — suggested model file.
- `app/models/__init__.py` or equivalent model registry — expose/import new models if required by repo pattern.
- `alembic/versions/<next_revision>_mobile_push_schema_settings_service.py` — migration following existing naming conventions after `20260423_0003_user_channel_import_contract.py`.
- `pyproject.toml` — include migration in package data/data-files only if existing migrations require explicit listing.
- `app/services/mobile_push.py` — service skeleton.
- Tests under the existing test structure for settings, migration/model schema smoke coverage, and service unit behavior.

Concise alternative model file organization is acceptable if it matches existing repository conventions.

## Data Model / Migration Details

Create all four final MVP tables from the cross-repo spec.

### `mobile_push_settings`

- `id`: integer primary key.
- `user_id`: integer, not null, FK `users.id` with delete cascade, unique.
- `enabled`: boolean, not null, default `false`.
- `default_for_monitored_channels`: boolean, not null, default `true`.
- `first_enabled_at`: timezone-aware datetime/timestamptz, nullable.
- `created_at`, `updated_at`: match existing `TimestampMixin` pattern where possible, not null, default current timestamp.
- Constraints/indexes: unique `(user_id)`.

### `mobile_push_installations`

- `id`: integer primary key.
- `user_id`: integer, not null, FK `users.id` with delete cascade.
- `installation_id`: UUID, not null; stable mobile-generated installation UUID.
- `expo_push_token`: text, not null; never expose raw in UI/logs.
- `platform`: varchar(20), not null, default `unknown`; expected values include `ios`, `android`, `unknown`.
- `app_version`: varchar(50), nullable.
- `build_number`: varchar(50), nullable.
- `device_name`: varchar(120), nullable.
- `enabled`: boolean, not null, default `true`.
- `registered_at`: timezone-aware datetime/timestamptz, not null, default current timestamp.
- `last_seen_at`: timezone-aware datetime/timestamptz, nullable.
- `unregistered_at`: timezone-aware datetime/timestamptz, nullable.
- `invalidated_at`: timezone-aware datetime/timestamptz, nullable.
- `last_attempt_at`: timezone-aware datetime/timestamptz, nullable.
- `last_success_at`: timezone-aware datetime/timestamptz, nullable.
- `last_error`: text, nullable, client-safe only.
- `last_expo_ticket_id`: varchar(255), nullable.
- `last_expo_status`: varchar(50), nullable.
- `last_receipt_checked_at`: timezone-aware datetime/timestamptz, nullable.
- `created_at`, `updated_at`: match existing `TimestampMixin` pattern where possible.
- Constraints/indexes: unique `(user_id, installation_id)` and index `(user_id, enabled)`.

### `mobile_push_channel_preferences`

- `id`: integer primary key.
- `user_id`: integer, not null, FK `users.id` with delete cascade.
- `channel_id`: integer, not null, FK `channels.id` with delete cascade.
- `push_enabled`: boolean, not null, default `true`.
- `explicitly_set`: boolean, not null, default `true`.
- `created_at`, `updated_at`: match existing `TimestampMixin` pattern where possible.
- Constraints/indexes: unique `(user_id, channel_id)`.
- Rows may remain when monitoring is disabled, but are ignored while `UserChannel.is_monitored=false`.

### `mobile_push_deliveries`

- `id`: integer primary key.
- `user_id`: integer, not null, FK `users.id` with delete cascade.
- `installation_id`: integer, not null, FK `mobile_push_installations.id` with delete cascade.
- `notification_delivery_id`: integer, nullable, FK `notification_deliveries.id`; null for test sends.
- `video_id`: integer, nullable, FK `videos.id`; set for new-video sends.
- `channel_id`: integer, nullable, FK `channels.id`; set for new-video sends.
- `event_type`: varchar(50), not null; values include `new_video` and `test`.
- `event_key`: varchar(255), nullable.
- `status`: varchar(50), not null, default `pending`; values include `pending`, `sent`, `failed`, `skipped`, `invalid_token`.
- `attempt_count`: integer, not null, default `0`.
- `last_attempt_at`: timezone-aware datetime/timestamptz, nullable.
- `last_success_at`: timezone-aware datetime/timestamptz, nullable.
- `last_error`: text, nullable, client-safe only.
- `expo_ticket_id`: varchar(255), nullable.
- `expo_status`: varchar(50), nullable.
- `expo_response`: JSON, nullable, sanitized provider metadata only.
- `created_at`, `updated_at`: match existing `TimestampMixin` pattern where possible.
- Constraints/indexes:
  - Unique new-video idempotency constraint on `(notification_delivery_id, installation_id)` where `notification_delivery_id IS NOT NULL`.
  - Optional unique test observability constraint on `(user_id, installation_id, event_key)` where `event_type='test' AND event_key IS NOT NULL` if supported consistently by the repository DB targets.
  - Index `(user_id, event_type, created_at)`.

## Settings Contract

Add these settings with disabled-by-default behavior:

| Env var | Default | Required | Notes |
| --- | --- | --- | --- |
| `PUSH_NOTIFICATIONS_ENABLED` | `false` | no | Runtime flag for actual provider sends. |
| `EXPO_PUSH_ENDPOINT` | `https://exp.host/--/api/v2/push/send` | no | Expo Push API endpoint for future sends. |
| `EXPO_PUSH_RECEIPTS_ENABLED` | `false` | no | Optional future receipt checking. |
| `EXPO_PUSH_RECEIPTS_ENDPOINT` | `https://exp.host/--/api/v2/push/getReceipts` | no | Future receipt endpoint. |
| `EXPO_ACCESS_TOKEN` | empty | no | Optional backend-only token; not required for MVP by default. |

`.env.example` must include the same variables without inventing secrets. `render.yaml` should include defaults for flags/endpoints. If `EXPO_ACCESS_TOKEN` is included in Render config, mark it `sync: false` and clarify no secret is required for MVP unless future Expo project configuration needs it.

## Service Skeleton Contract

Create `app/services/mobile_push.py` with network-free, testable contracts for future phases. Suggested contracts may use dataclasses/DTOs where useful.

- Constants/status values:
  - Event types: `new_video`, `test`.
  - Delivery statuses: `pending`, `sent`, `failed`, `skipped`, `invalid_token`.
  - Platforms: `ios`, `android`, `unknown` if centralized.
- Global settings:
  - Get or create the single-user/global `MobilePushSetting` row with `enabled=false` and `default_for_monitored_channels=true` by default.
  - Preserve `first_enabled_at` semantics for later phases if helper is included.
- Token masking:
  - Provide `mask_expo_token(token: str | None) -> str | None` for status responses.
  - Never return raw Expo tokens from status-oriented helpers.
- Effective channel eligibility/preference:
  - Compute `push_eligible` and `push_enabled` using global settings, `UserChannel.is_monitored`, and optional explicit preference.
  - Rule: `settings.enabled AND user_channel.is_monitored AND (preference.push_enabled if explicitly set else settings.default_for_monitored_channels)`.
- Installation helpers:
  - Provide an upsert/register helper contract placeholder or skeleton that updates token, platform, app metadata, `enabled`, registration timestamps, and clears unregister state as appropriate.
  - Provide disable/unregister helper contract placeholder or skeleton that is idempotent and sets `enabled=false`/`unregistered_at` when found.
- Delivery ledger helpers:
  - Provide create/find helper by `(notification_delivery_id, installation_id)` for future new-video idempotency.
  - Allow test-delivery helper shape if useful, but no endpoint behavior is required.
- Payload builders:
  - Build Expo payload shapes for `new_video` and `test` without sending.
  - Payload `data` must contain no bearer tokens, internal tokens, provider credentials, raw Expo token, internal URLs, stack traces, or sensitive diagnostics.
- Sender abstraction / disabled behavior:
  - Expose a future sender abstraction or `send_*` placeholder that does not perform real network sends in 11A.
  - If provider sending is disabled by `PUSH_NOTIFICATIONS_ENABLED=false`, return a no-op/skipped/disabled result that future phases can handle safely.

Do not wire this service into polling and do not add API routes in Phase 11A.

## Implementation Steps

1. Review the current settings, model, migration, and package-data conventions.
2. Add push settings to `app/core/settings.py` with safe defaults and optional `EXPO_ACCESS_TOKEN`.
3. Update `.env.example` and `render.yaml` with push env vars and no invented secrets.
4. Add SQLAlchemy models for all four mobile push tables using existing timestamp and relationship conventions.
5. Add an Alembic migration after `20260423_0003_user_channel_import_contract.py` following current revision naming conventions.
6. Update model registry/imports and `pyproject.toml` migration data inclusion only if required by the repo pattern.
7. Add `app/services/mobile_push.py` with constants, helpers, payload builders, and disabled-provider no-op behavior.
8. Add tests for settings defaults, migration/model schema smoke coverage, and service helper behavior.
9. Run verification and fix only Phase 11A issues.

## Tests And Verification

Required tests:

- [ ] Migration/model schema smoke tests prove all four tables exist.
- [ ] Schema tests cover enough core FKs, unique constraints, indexes, defaults, and nullable rules to catch regressions.
- [ ] Settings tests prove default push env values are safe and disabled by default.
- [ ] Settings tests cover runtime validation safety if the settings class validates endpoint URLs or booleans.
- [ ] Service unit tests cover Expo token masking and no raw token exposure in status-oriented helpers.
- [ ] Service unit tests cover default settings creation.
- [ ] Service unit tests cover the effective preference rule for global disabled, unmonitored channel, inherited default, explicit enable, and explicit disable.
- [ ] Service unit tests cover registration/upsert helper if implemented.
- [ ] Service unit tests cover unregister/disable helper if implemented.
- [ ] Service unit tests cover delivery idempotency helper if implemented.
- [ ] Service unit tests cover `new_video` and `test` payload builders and verify no secrets/raw Expo tokens in payload `data`.
- [ ] Existing suite still passes.

Suggested verification commands using existing repo style:

```powershell
python -m pytest
python -m alembic upgrade head --sql
.venv\Scripts\python.exe -m build
```

Use the build command if previous phases/package verification require it. Do not invent secrets for local or Render verification.

## Acceptance Criteria

- [ ] `PUSH_NOTIFICATIONS_ENABLED`, `EXPO_PUSH_ENDPOINT`, `EXPO_PUSH_RECEIPTS_ENABLED`, `EXPO_PUSH_RECEIPTS_ENDPOINT`, and optional backend-only `EXPO_ACCESS_TOKEN` are represented in settings/env examples/deployment config with safe defaults.
- [ ] `mobile_push_settings`, `mobile_push_installations`, `mobile_push_channel_preferences`, and `mobile_push_deliveries` models and migration exist.
- [ ] New tables include the required fields, FKs, unique constraints, indexes, defaults, and nullable rules from this spec.
- [ ] Migration is discoverable by Alembic and included in package data if the repository pattern requires it.
- [ ] Service skeleton is ready for Phase 11B endpoints and Phase 11C polling integration.
- [ ] Service skeleton includes safe disabled-provider behavior when `PUSH_NOTIFICATIONS_ENABLED=false`.
- [ ] Service skeleton can build `new_video` and `test` payload shapes without performing a real Expo send.
- [ ] Payload helpers do not include bearer tokens, internal tokens, provider credentials, raw Expo token in `data`, internal URLs, stack traces, or sensitive diagnostics.
- [ ] New settings, schema, and service helper tests pass.
- [ ] Existing suite still passes.
- [ ] No endpoints are added in 11A.
- [ ] No polling trigger is added in 11A.
- [ ] No real Expo sends are required or implemented in 11A.
- [ ] Existing mobile API contracts remain unchanged.

## Handoff Notes For Phase 11B

- Phase 11B must add `/internal/mobile-push/*` endpoints using `require_mobile_bearer_token` only.
- Phase 11B should reuse the 11A service helpers for status masking, registration/unregister, global settings, channel preference calculation, and test payload setup.
- Phase 11B must preserve existing `/internal/channels`, `/internal/activity`, and `/status` DTOs.
- Phase 11C, not 11B, wires push into the polling new-video branch and performs real best-effort Expo sends if approved and enabled.
