# Backend Phase 11B Mobile Push Endpoints, Status, Preferences, Test Specification

## Context

Phase 11B implements the mobile-push API surface defined by `specs/mobile_push_notifications_backend_cross_repo.spec.md`, building on the Phase 11A schema/settings/service foundation in `app/services/mobile_push.py` and `app/models/mobile_push_*`. This phase adds endpoints for registration, status, global settings, channel preferences, and manual test sends.

Phase 11B may perform a real synchronous Expo Push Service send only for `POST /internal/mobile-push/test` when `PUSH_NOTIFICATIONS_ENABLED=true`. Phase 11C owns polling integration, new-video fan-out, and idempotent new-video send behavior.

## Requirements

- [ ] Add `app/api/routes/mobile_push.py` with prefix `/internal/mobile-push`.
- [ ] Include the mobile-push router in `app/main.py`.
- [ ] Protect every mobile-push endpoint with `require_mobile_bearer_token` only.
- [ ] Explicitly do not use `require_admin_bearer_token` or `require_internal_bearer_token` for these endpoints.
- [ ] Implement endpoint DTOs/request/response models for status, register, unregister, settings, channel preferences, channel preference update, and test send.
- [ ] Reuse and extend `app/services/mobile_push.py` helpers from 11A as needed.
- [ ] Preserve existing `/internal/channels`, `/internal/activity`, `/status`, polling endpoint, and auth contracts.
- [ ] Set `first_enabled_at` only once on first transition from global disabled to enabled.
- [ ] Implement channel preference list/query/pagination without changing existing `/internal/channels` DTO.
- [ ] Allow explicit channel preference updates only for currently monitored channels.
- [ ] Return unknown channel `404`; return unmonitored channel `409` with client-safe detail.
- [ ] Return installation status with masked Expo token only.
- [ ] For unknown installation status, return `registered:false` and do not expose other device data.
- [ ] Implement registration upsert and unregister idempotency.
- [ ] Implement status summary fields from installation delivery summary columns.
- [ ] Implement real Expo send for `/test` only when `PUSH_NOTIFICATIONS_ENABLED=true`.
- [ ] If `PUSH_NOTIFICATIONS_ENABLED=false`, `/test` returns clear disabled behavior, preferably `409`, and makes no network call.
- [ ] Use existing `httpx`; do not add an Expo SDK dependency.
- [ ] Include optional `Authorization: Bearer <EXPO_ACCESS_TOKEN>` to Expo only when configured.
- [ ] Never log or return raw Expo tokens, bearer tokens, provider credentials, or unsanitized provider responses.
- [ ] Record test push delivery rows with `event_type='test'`.
- [ ] Update installation summary fields: `last_attempt_at`, `last_success_at`, `last_error`, `last_expo_ticket_id`, `last_expo_status` with sanitized data.
- [ ] Handle Expo success/error response shapes robustly and client-safely.
- [ ] Provider failure may return `502`.
- [ ] Invalid Expo token should disable/mark the installation and return a client-safe failure.
- [ ] Add tests for auth, DTOs, registration, unregister, settings, channel preferences, test endpoint disabled/success/failure behavior, and no secret/raw-token exposure.

## Non-Goals / Out Of Scope

- [ ] Do not integrate with `YouTubePollingService.run_poll`.
- [ ] Do not trigger push on new videos.
- [ ] Do not fan out to all installations for new videos.
- [ ] Do not change `/internal/channels`, `/internal/activity`, `/status`, existing polling endpoint, or existing auth contracts.
- [ ] Do not add Celery, Redis, background workers, queues, or Expo SDK dependency.
- [ ] Do not implement receipt polling unless trivially represented as not enabled/status-only; actual receipt polling is post-MVP unless separately approved.

## Technical Approach

Create a dedicated FastAPI router for mobile-push APIs and centralize business logic in `app/services/mobile_push.py`. The router should be thin: validate DTOs, apply `require_mobile_bearer_token`, call service helpers, map service outcomes to stable HTTP responses, and avoid logging sensitive inputs.

Use the existing single-user/backend owner pattern from prior internal APIs. Channel preference reads should join the channel catalog and `UserChannel` monitoring state, optionally left-joining explicit push preference rows. Preference output must be separate from existing channel catalog DTOs.

For test sends, use synchronous `httpx` against `EXPO_PUSH_ENDPOINT` only when runtime sending is enabled. Treat Expo ticket acceptance (`status: ok`) as success; treat provider HTTP errors, malformed responses, and Expo `status: error` as sanitized failures. Known invalid-token errors should disable/mark the installation.

## Files To Create Or Modify

- `app/api/routes/mobile_push.py` — new router and endpoint DTOs, or route-local DTO imports if repository conventions use separate schema modules.
- `app/main.py` — include the new router.
- `app/services/mobile_push.py` — extend 11A helpers for endpoint behavior, status summaries, channel preferences, registration/unregister, and test send.
- Tests, likely `tests/test_mobile_push_api.py` and/or additions to `tests/test_mobile_push_11a.py`.

Do not modify product files outside this scope except as required to wire and test the 11B API.

## Endpoint Contracts

All endpoints require:

```http
Authorization: Bearer <MOBILE_API_BEARER_TOKEN>
```

### `GET /internal/mobile-push/status?installation_id=<uuid>`

- Returns global settings, installation registration/device metadata, and delivery summary fields.
- Unknown installation returns `200` with `registered:false`, `enabled:false`, null metadata fields, null delivery fields, and no data from other installations.
- `token_masked` is the only token-like field and must never contain the raw Expo token.
- Invalid UUID/query validation returns `422`.

### `POST /internal/mobile-push/register`

Request fields:

- `installation_id` UUID, required.
- `expo_push_token` string, required.
- `platform` string, expected `ios`, `android`, or `unknown`.
- `app_version`, `build_number`, `device_name` optional bounded strings.

Behavior:

- Upsert by `(user_id, installation_id)`.
- Update rotatable Expo token and device metadata.
- Set installation `enabled=true`, update `registered_at`/`last_seen_at`, clear unregister/invalid state as appropriate.
- Return masked token and global enabled state.
- Do not return raw token.

### `DELETE /internal/mobile-push/installations/{installation_id}`

- Idempotently disable/unregister an installation.
- Known installation: set `enabled=false` and `unregistered_at`.
- Unknown installation: return success with `registered:false`, `enabled:false`.
- Do not require current Expo token in the request.

### `PATCH /internal/mobile-push/settings`

Request fields:

- `enabled` optional boolean.
- `default_for_monitored_channels` optional boolean if supported by 11A model/service; otherwise preserve current value.

Behavior:

- Create default settings row if missing.
- Global push is disabled by default.
- Set `first_enabled_at` only when transitioning from disabled to enabled and it is currently null.
- Never reset `first_enabled_at` on disable/re-enable.
- Return effective settings and `monitored_channels_effectively_enabled_count`.

### `GET /internal/mobile-push/channel-preferences?monitoring=monitored|all&query=&limit=&offset=`

- Default `monitoring=monitored`.
- `monitoring=all` may include unmonitored channels for diagnostics/future UI.
- `query` filters channel title and/or YouTube channel ID consistently with existing channel search behavior where practical.
- `limit` and `offset` follow existing pagination defaults/bounds where practical.
- Return each channel with `channel_id`, `youtube_channel_id`, `title`, `is_monitored`, `push_eligible`, `push_enabled`, and `preference` details.
- Do not change the existing `/internal/channels` DTO.

Effective rule:

```text
push_enabled =
  global_settings.enabled
  AND user_channel.is_monitored
  AND (explicit preference if explicitly_set else global_settings.default_for_monitored_channels)
```

### `PATCH /internal/mobile-push/channels/{channel_id}`

Request fields:

- `push_enabled` boolean, required.

Behavior:

- Unknown channel returns `404`.
- Existing but not currently monitored channel returns `409` with clear client-safe detail.
- Monitored channel upserts explicit preference with `explicitly_set=true`.
- Return the same channel preference shape used by the list endpoint.

### `POST /internal/mobile-push/test`

Request fields:

- `installation_id` UUID, required.

Behavior:

- If `PUSH_NOTIFICATIONS_ENABLED=false`, return clear disabled behavior, preferably HTTP `409`, and make no network call.
- If installation is unknown, unregistered, disabled, invalidated, or lacks a token, return client-safe `409` unless implementation chooses a documented `404` for unknown installation.
- If enabled, send one synchronous Expo test payload to the requested installation only.
- Create a `mobile_push_deliveries` row with `event_type='test'` and sanitized status fields.
- Update installation summary fields for attempt/success/error/ticket/status.
- Success returns `sent:true`, `event_type:'test'`, `last_attempt_at`, `expo_status`, optional `expo_ticket_id`, and a client-safe message.
- Provider HTTP failure, timeout, malformed response, or Expo error may return `502` with client-safe detail.
- Known invalid-token/provider device-not-registered errors should disable or mark the installation, record `invalid_token`/failure state, and not expose the raw token or provider credentials.

## Expo Sender Behavior For Test Endpoint

- Use existing `httpx`; no new provider SDK.
- POST to `settings.EXPO_PUSH_ENDPOINT`.
- Payload shape:

```json
{
  "to": "<raw expo token sent only to Expo>",
  "title": "YTPipe test notification",
  "body": "Push notifications are connected.",
  "sound": "default",
  "data": {
    "type": "test",
    "sent_at": "<iso timestamp>"
  }
}
```

- Add `Authorization: Bearer <EXPO_ACCESS_TOKEN>` only if `EXPO_ACCESS_TOKEN` is configured.
- Do not include bearer tokens, internal tokens, provider credentials, raw Expo token, internal URLs, stack traces, or sensitive diagnostics in payload `data`, API responses, persisted sanitized response data, or logs.
- Parse common Expo response shapes, including `data` object or array entries with `status`, `id`, `message`, and `details.error`.
- Treat `status: ok` as success and persist ticket ID when present.
- Treat `DeviceNotRegistered` or equivalent invalid-token errors as invalid token handling.

## Data Updates / Transactions

- Registration upsert, unregister, settings update, channel preference update, and test delivery state changes should be transactionally consistent.
- Test send may require recording attempt before the network call and final status after the response; ensure failure paths still persist a sanitized delivery/installation summary where practical.
- Do not roll back persisted registration/settings/preference changes because of unrelated provider failures.
- `mobile_push_deliveries.notification_delivery_id`, `video_id`, and `channel_id` remain null for test sends.
- New-video idempotency rows are not created in 11B except test rows with `event_type='test'`.

## Security Requirements

- Use `require_mobile_bearer_token` only for all 11B endpoints.
- Reject missing, invalid, and internal-only bearer tokens in tests.
- Mobile-push endpoints must not accept or require `INTERNAL_API_BEARER_TOKEN`.
- Never return raw Expo token; expose masked token only.
- Never log bearer tokens, Expo tokens, `EXPO_ACCESS_TOKEN`, provider credentials, or raw provider responses that may contain sensitive data.
- Client-facing errors must be sanitized and must not include stack traces, internal URLs, tokens, or credentials.
- OpenAPI behavior should remain compatible with existing protected `/internal/*` security metadata.

## Implementation Steps

1. Review 11A models/service helpers and existing FastAPI router/dependency conventions.
2. Add `app/api/routes/mobile_push.py` with DTOs and all seven endpoint handlers.
3. Include the router in `app/main.py`.
4. Extend `app/services/mobile_push.py` for settings, status, registration, unregister, channel preference list/update, token masking, and test-send orchestration.
5. Add a small Expo sender/helper using `httpx`, gated by `PUSH_NOTIFICATIONS_ENABLED`.
6. Add sanitized provider response parsing and invalid-token handling.
7. Add API/service tests for the 11B scope.
8. Run verification and fix only Phase 11B issues.

## Testing And Verification

Required tests:

- [ ] Auth tests prove every endpoint accepts only `MOBILE_API_BEARER_TOKEN` and rejects missing/wrong/internal-only auth.
- [ ] DTO validation tests cover invalid UUIDs, request bodies, query enum, pagination bounds, and path parameters.
- [ ] Register tests cover create, update/upsert, token rotation, metadata update, and masked-token response.
- [ ] Unregister tests cover known installation and unknown installation idempotency.
- [ ] Status tests cover known installation, unknown installation, masked token, global settings, and delivery summary fields.
- [ ] Settings tests cover default disabled state, enable, disable, and `first_enabled_at` set only once.
- [ ] Channel preference list tests cover monitored default, `monitoring=all`, search, pagination, inherited default, explicit enable, explicit disable, and global-disabled effective false.
- [ ] Channel preference update tests cover monitored success, unknown channel `404`, and unmonitored channel `409`.
- [ ] Test endpoint disabled test proves `PUSH_NOTIFICATIONS_ENABLED=false` returns clear disabled behavior and no network call.
- [ ] Test endpoint success test mocks Expo `status: ok`, records `event_type='test'`, updates installation summary, and returns client-safe success.
- [ ] Test endpoint failure tests cover provider HTTP/error shape/malformed response with `502` or client-safe failure.
- [ ] Invalid-token test disables/marks installation and persists sanitized failure.
- [ ] Secret exposure tests prove responses, payload `data`, and persisted sanitized fields contain no raw Expo token, bearer token, `EXPO_ACCESS_TOKEN`, internal token, internal URL, stack trace, or provider credential.
- [ ] Regression tests prove `/internal/channels`, `/internal/activity`, `/status`, existing polling endpoint, and existing auth contracts are unchanged.
- [ ] Negative integration test proves Phase 11B does not call or modify `YouTubePollingService.run_poll` behavior and does not trigger new-video push fan-out.

Suggested verification commands for `@tech-lead`:

```powershell
python -m pytest tests/test_mobile_push_api.py
python -m pytest tests/test_mobile_push_11a.py tests/test_mobile_push_api.py
python -m pytest
```

Use repository-specific test names if the implementation chooses a different test file organization.

## Acceptance Criteria

- [ ] `app/api/routes/mobile_push.py` exists and exposes all seven `/internal/mobile-push` endpoints.
- [ ] `app/main.py` includes the mobile-push router.
- [ ] Every mobile-push endpoint uses `require_mobile_bearer_token` only.
- [ ] No mobile-push endpoint uses `require_admin_bearer_token` or `require_internal_bearer_token`.
- [ ] Existing `/internal/channels`, `/internal/activity`, `/status`, polling endpoint, and auth contracts remain unchanged.
- [ ] Status returns masked token only and unknown installations return `registered:false` without other device data.
- [ ] Registration is an idempotent upsert and unregister is idempotent.
- [ ] Global settings are disabled by default and `first_enabled_at` is set only once on first enable.
- [ ] Channel preferences are listed/paginated separately from `/internal/channels` and follow the effective preference rule.
- [ ] Channel preference update returns `404` for unknown channels and `409` for unmonitored channels.
- [ ] `/test` makes no network call and returns clear disabled behavior when `PUSH_NOTIFICATIONS_ENABLED=false`.
- [ ] `/test` performs a real synchronous Expo send through `httpx` when enabled.
- [ ] `/test` records `event_type='test'` delivery rows and updates installation summary fields with sanitized data.
- [ ] Expo provider success, provider failure, malformed response, timeout, and invalid-token cases are handled client-safely.
- [ ] No raw Expo token, bearer token, internal token, `EXPO_ACCESS_TOKEN`, provider credential, internal URL, stack trace, or sensitive diagnostic is returned, logged, placed in payload `data`, or persisted unsanitized.
- [ ] No polling trigger, new-video fan-out, or `YouTubePollingService.run_poll` integration is implemented in 11B.
- [ ] Required 11B tests pass, and the full existing suite still passes.

## Handoff Notes For Phase 11C

- Reuse the 11B Expo sender/parsing/sanitization path for new-video sends.
- Phase 11C must integrate only at the polling new-video branch after canonical `Video` and email/activity `NotificationDelivery` exist.
- Phase 11C must fan out to enabled/registered installations only when global settings, monitoring, and effective channel preference allow it.
- Phase 11C owns idempotent new-video delivery behavior using `(notification_delivery_id, installation_id)` and must not duplicate sends.
- Phase 11C push failures must be recorded but must not fail or roll back polling.
