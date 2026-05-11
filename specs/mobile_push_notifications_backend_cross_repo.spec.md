# YTPipe Mobile Push Notifications Backend/Cross-Repo Specification

## Status

- [x] This is the definitive backend/cross-repo MVP specification for YTPipe mobile push notifications.
- [x] This specification supersedes `specs/mobile_push_notifications_master_cross_repo_draft.spec.md` for backend planning, backend implementation specs, and mobile-repo contract reference.
- [x] The master draft remains useful historical context, but backend planning must follow this document where details differ.
- [x] No product code is implemented by this specification.

## Context

YTPipe is a single-user personal YouTube subscription notifier. The backend imports a channel catalog, lets the owner explicitly mark channels as monitored, polls only monitored channels, detects new uploads, and records email/activity delivery state.

Mobile push notifications are future backend and mobile work. MVP push must:

- Notify for new videos detected on monitored channels only.
- Use Expo Push Service.
- Support multiple mobile installations for the same single-user backend owner.
- Use existing mobile bearer authentication only.
- Preserve the current mobile contract that the mobile app never receives or uses `INTERNAL_API_BEARER_TOKEN`.
- Avoid changing existing `/internal/channels` and `/internal/activity` DTOs for MVP unless a later spec explicitly approves it.

## Evidence From Current Backend

- `app/core/settings.py` currently defines settings for `INTERNAL_API_BEARER_TOKEN`, `MOBILE_API_BEARER_TOKEN`, database, quota, and email. No push settings exist yet.
- `app/api/dependencies.py` provides `require_mobile_bearer_token`, `require_internal_bearer_token`, and `require_admin_bearer_token`. Mobile-push endpoints must use `require_mobile_bearer_token` only.
- Existing admin endpoints may use `require_admin_bearer_token`, which accepts either mobile or internal tokens. Push registration, preferences, status, unregister, and test must not use that dependency because QStash/internal auth is not device-management auth.
- `app/main.py` manually includes routers. A future `mobile_push` router must be registered there. Because the planned paths are under `/internal/`, OpenAPI security metadata will be applied automatically by the existing protected-prefix logic.
- `specs/mobile_api_contract_for_rn.spec.md` says push does not exist yet, mobile uses `MOBILE_API_BEARER_TOKEN`, and the internal/QStash token must not be exposed.
- Current models include:
  - `Channel`: `id`, `youtube_channel_id`, `title`, `uploads_playlist_id`.
  - `UserChannel`: `user_id`, `channel_id`, `is_monitored`, `last_seen_video_id`, `baseline_established_at`; imported channels default unmonitored after migration `0003`.
  - `Video`: `id`, `youtube_video_id`, `channel_id`, `title`, `published_at`.
  - `NotificationDelivery`: email/activity ledger with unique `(user_id, video_id)`, status, attempt count, last attempt, and last error. Existing Activity API treats `notification_deliveries.id` as `activity_id`/`delivery_id`.
  - `SyncState`: process state metadata.
- `app/services/polling.py` processes only `UserChannel.is_monitored == True` rows, establishes baselines without creating video/delivery records, and creates canonical `Video` plus email/activity `NotificationDelivery` only in the new-video branch.
- Current polling processes existing pending/retry email deliveries before channel polling. Those existing delivery retries must not produce new push notifications.
- The correct MVP push insertion point is the new-video branch after canonical `Video` and email/activity `NotificationDelivery` exist. Push must not fire for baseline, unchanged latest video, email retry processing, unmonitored channels, quota/safety block, sync failure, poll failure, or email delivery failure.
- `POST /internal/run-poll` uses the admin bearer dependency today. Manual mobile poll and QStash/internal automatic poll share `YouTubePollingService.run_poll`, so both must trigger push identically when the new-video branch is reached.
- `GET /internal/channels` has a stable DTO without push fields. MVP uses a separate channel-preferences endpoint instead of changing that DTO.
- `GET /internal/activity` reads email/activity records only. MVP push taps navigate to Activity and refetch the existing list; payload IDs are optional context only.
- `.env.example` and `render.yaml` currently have no push environment variables.
- `pyproject.toml` already includes `httpx`, which is sufficient for basic Expo Push API calls. No mandatory provider SDK dependency is required for MVP.

## Final Decisions

- [x] Provider: Expo Push Service for MVP.
- [x] Auth: every mobile-push endpoint uses `Authorization: Bearer <MOBILE_API_BEARER_TOKEN>` and the backend dependency `require_mobile_bearer_token`.
- [x] Mobile must never use `INTERNAL_API_BEARER_TOKEN`.
- [x] Endpoint set:
  - `GET /internal/mobile-push/status?installation_id=<uuid>`
  - `POST /internal/mobile-push/register`
  - `DELETE /internal/mobile-push/installations/{installation_id}`
  - `PATCH /internal/mobile-push/settings`
  - `GET /internal/mobile-push/channel-preferences?monitoring=monitored|all&query=&limit=&offset=`
  - `PATCH /internal/mobile-push/channels/{channel_id}`
  - `POST /internal/mobile-push/test`
- [x] MVP storage tables: `mobile_push_settings`, `mobile_push_installations`, `mobile_push_channel_preferences`, and `mobile_push_deliveries`.
- [x] Sending strategy: synchronous best-effort send during poll/test. No Celery, Redis, new worker, or required provider SDK.
- [x] Push send failure during polling must be recorded and must not fail or roll back the poll.
- [x] Queue/outbox/background processing is post-MVP unless reliability requirements change.
- [x] Dedicated mobile-push status endpoint is required. Changing `/status` is not required for MVP.

## Backend API Contract

All endpoints in this section are protected by:

```http
Authorization: Bearer <MOBILE_API_BEARER_TOKEN>
```

All endpoints must use `require_mobile_bearer_token`. Do not use `require_admin_bearer_token` or `require_internal_bearer_token` for these endpoints.

### `GET /internal/mobile-push/status?installation_id=<uuid>`

Returns global push settings, current installation state, and recent delivery diagnostics. If the installation is unknown, return `registered: false` rather than exposing another installation's data.

Response example:

```json
{
  "global": {
    "enabled": true,
    "default_for_monitored_channels": true,
    "first_enabled_at": "2026-05-08T12:00:00Z",
    "updated_at": "2026-05-08T12:00:00Z"
  },
  "installation": {
    "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
    "registered": true,
    "enabled": true,
    "platform": "ios",
    "app_version": "1.0.0",
    "build_number": "42",
    "device_name": "Owner iPhone",
    "token_masked": "ExponentPushToken[abcd…wxyz]",
    "last_registered_at": "2026-05-08T12:00:00Z",
    "last_seen_at": "2026-05-08T12:00:00Z",
    "last_unregistered_at": null
  },
  "delivery": {
    "last_attempt_at": "2026-05-08T12:05:00Z",
    "last_success_at": "2026-05-08T12:05:02Z",
    "last_error": null,
    "last_expo_ticket_id": "ticket-id-placeholder",
    "last_expo_status": "ok",
    "last_receipt_checked_at": null
  }
}
```

Unknown installation response example:

```json
{
  "global": {
    "enabled": false,
    "default_for_monitored_channels": true,
    "first_enabled_at": null,
    "updated_at": null
  },
  "installation": {
    "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
    "registered": false,
    "enabled": false,
    "platform": null,
    "app_version": null,
    "build_number": null,
    "device_name": null,
    "token_masked": null,
    "last_registered_at": null,
    "last_seen_at": null,
    "last_unregistered_at": null
  },
  "delivery": {
    "last_attempt_at": null,
    "last_success_at": null,
    "last_error": null,
    "last_expo_ticket_id": null,
    "last_expo_status": null,
    "last_receipt_checked_at": null
  }
}
```

### `POST /internal/mobile-push/register`

Registers or updates the current installation. Idempotent by `(user_id, installation_id)`. Expo tokens are rotatable; a repeated registration updates the stored token and device metadata.

Request example:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "expo_push_token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "platform": "ios",
  "app_version": "1.0.0",
  "build_number": "42",
  "device_name": "Owner iPhone"
}
```

Response example:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "registered": true,
  "enabled": true,
  "global_enabled": false,
  "token_masked": "ExponentPushToken[xxxx…xxxx]",
  "last_registered_at": "2026-05-08T12:00:00Z"
}
```

### `DELETE /internal/mobile-push/installations/{installation_id}`

Unregisters the current installation. This operation is idempotent. Unknown installation IDs return success with `registered: false`.

Response example:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "registered": false,
  "enabled": false,
  "unregistered_at": "2026-05-08T12:10:00Z"
}
```

### `PATCH /internal/mobile-push/settings`

Updates global push settings. Global push is disabled by default. On first global enable, currently monitored channels become effectively push-enabled by default through `default_for_monitored_channels=true`; explicit channel disables remain respected.

Request example:

```json
{
  "enabled": true
}
```

Response example:

```json
{
  "enabled": true,
  "default_for_monitored_channels": true,
  "first_enabled_at": "2026-05-08T12:00:00Z",
  "updated_at": "2026-05-08T12:00:00Z",
  "monitored_channels_effectively_enabled_count": 12
}
```

### `GET /internal/mobile-push/channel-preferences?monitoring=monitored|all&query=&limit=&offset=`

Reads per-channel effective push state without changing the stable `GET /internal/channels` DTO. Default `monitoring` is `monitored`. `monitoring=all` may include unmonitored channels for diagnostics or future UI states. `limit` and `offset` follow the existing channels pagination pattern.

Response example:

```json
{
  "channels": [
    {
      "channel_id": 123,
      "youtube_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
      "title": "Example Channel",
      "is_monitored": true,
      "push_eligible": true,
      "push_enabled": true,
      "preference": {
        "explicitly_set": false,
        "explicit_push_enabled": null,
        "updated_at": null
      }
    },
    {
      "channel_id": 124,
      "youtube_channel_id": "UCyyyyyyyyyyyyyyyyyyyyyy",
      "title": "Muted Channel",
      "is_monitored": true,
      "push_eligible": true,
      "push_enabled": false,
      "preference": {
        "explicitly_set": true,
        "explicit_push_enabled": false,
        "updated_at": "2026-05-08T12:30:00Z"
      }
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "total": 2
  }
}
```

### `PATCH /internal/mobile-push/channels/{channel_id}`

Sets an explicit per-channel push preference. The channel must exist and must currently be monitored. Enabling or disabling push for an unmonitored channel returns `409` with a clear client-safe detail.

Request example:

```json
{
  "push_enabled": false
}
```

Response example:

```json
{
  "channel_id": 123,
  "is_monitored": true,
  "push_eligible": true,
  "push_enabled": false,
  "preference": {
    "explicitly_set": true,
    "explicit_push_enabled": false,
    "updated_at": "2026-05-08T12:30:00Z"
  }
}
```

Unmonitored-channel error example:

```json
{
  "detail": "Channel is not monitored and is not eligible for push preferences."
}
```

### `POST /internal/mobile-push/test`

Sends a synchronous best-effort test push to the current installation. This endpoint may return `502` if Expo/provider send fails and there is no success/queued state to report.

Request example:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21"
}
```

Success response example:

```json
{
  "sent": true,
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "event_type": "test",
  "last_attempt_at": "2026-05-08T12:15:00Z",
  "expo_status": "ok",
  "expo_ticket_id": "ticket-id-placeholder",
  "message": "Test notification sent."
}
```

Provider-failure response example:

```json
{
  "detail": "Expo push send failed for the requested installation."
}
```

## Data Model And Migration Plan

Create a future Alembic migration for the following tables. Names are final for MVP.

### `mobile_push_settings`

Purpose: one global push settings row per backend user.

| Column | Type | Null | Default | Constraints / Notes |
| --- | --- | --- | --- | --- |
| `id` | integer | no | identity | Primary key. |
| `user_id` | integer | no | none | FK `users.id` on delete cascade; unique. |
| `enabled` | boolean | no | `false` | Global push disabled by default. |
| `default_for_monitored_channels` | boolean | no | `true` | Used when no explicit channel preference exists. |
| `first_enabled_at` | timestamptz | yes | null | Set once on first transition to enabled. |
| `created_at` | timestamptz | no | now | Match existing timestamp mixin style. |
| `updated_at` | timestamptz | no | now | Update on settings changes. |

Constraints:

- Unique: `(user_id)`.
- `enabled=false` must make all channel preferences ineffective without deleting preference rows.

### `mobile_push_installations`

Purpose: one row per mobile app installation/device registered to receive Expo pushes.

| Column | Type | Null | Default | Constraints / Notes |
| --- | --- | --- | --- | --- |
| `id` | integer | no | identity | Primary key. |
| `user_id` | integer | no | none | FK `users.id` on delete cascade. |
| `installation_id` | uuid | no | none | Stable UUID generated by mobile. |
| `expo_push_token` | text | no | none | Sensitive-ish; never expose raw in UI/logs. |
| `platform` | varchar(20) | no | `unknown` | Allowed values should include `ios`, `android`, `unknown`. |
| `app_version` | varchar(50) | yes | null | Optional app version. |
| `build_number` | varchar(50) | yes | null | Optional build number. |
| `device_name` | varchar(120) | yes | null | Optional local/mobile-provided label. |
| `enabled` | boolean | no | `true` | False after unregister or invalid token. |
| `registered_at` | timestamptz | no | now | Last registration/update time. |
| `last_seen_at` | timestamptz | yes | null | Update on register/status/test if useful. |
| `unregistered_at` | timestamptz | yes | null | Set on unregister. |
| `invalidated_at` | timestamptz | yes | null | Set when Expo reports invalid/unregistered token. |
| `last_attempt_at` | timestamptz | yes | null | Summary status for Settings. |
| `last_success_at` | timestamptz | yes | null | Summary status for Settings. |
| `last_error` | text | yes | null | Client-safe error summary; no secrets. |
| `last_expo_ticket_id` | varchar(255) | yes | null | Ticket ID or summary. |
| `last_expo_status` | varchar(50) | yes | null | Example: `ok`, `error`. |
| `last_receipt_checked_at` | timestamptz | yes | null | Only if receipts are implemented. |
| `created_at` | timestamptz | no | now | Timestamp. |
| `updated_at` | timestamptz | no | now | Timestamp. |

Constraints:

- Unique: `(user_id, installation_id)`.
- Index: `(user_id, enabled)` for send fan-out.
- Registration is an upsert by `(user_id, installation_id)`.

### `mobile_push_channel_preferences`

Purpose: optional explicit per-channel push preference. Absence means inherit the global default for monitored channels.

| Column | Type | Null | Default | Constraints / Notes |
| --- | --- | --- | --- | --- |
| `id` | integer | no | identity | Primary key. |
| `user_id` | integer | no | none | FK `users.id` on delete cascade. |
| `channel_id` | integer | no | none | FK `channels.id` on delete cascade. |
| `push_enabled` | boolean | no | `true` | Explicit preference value when row exists. |
| `explicitly_set` | boolean | no | `true` | Preserves explicit disables/enables across global changes. |
| `created_at` | timestamptz | no | now | Timestamp. |
| `updated_at` | timestamptz | no | now | Timestamp. |

Constraints:

- Unique: `(user_id, channel_id)`.
- Preference rows may remain when monitoring is disabled, but are ignored while `UserChannel.is_monitored=false`.

Effective rule:

```text
effective_push_enabled =
  mobile_push_settings.enabled
  AND user_channels.is_monitored
  AND (
    mobile_push_channel_preferences.push_enabled
    if explicitly_set preference row exists
    else mobile_push_settings.default_for_monitored_channels
  )
```

When monitoring is disabled, push is not effective. When monitoring is re-enabled, the channel inherits the global default unless an explicit preference row exists.

### `mobile_push_deliveries`

Purpose: idempotency and observability ledger for push sends. This is not a user-facing delivery history screen in MVP.

| Column | Type | Null | Default | Constraints / Notes |
| --- | --- | --- | --- | --- |
| `id` | integer | no | identity | Primary key. |
| `user_id` | integer | no | none | FK `users.id` on delete cascade. |
| `installation_id` | integer | no | none | FK `mobile_push_installations.id` on delete cascade. |
| `notification_delivery_id` | integer | yes | null | FK `notification_deliveries.id`; null for test sends. |
| `video_id` | integer | yes | null | FK `videos.id`; set for new-video sends. |
| `channel_id` | integer | yes | null | FK `channels.id`; set for new-video sends. |
| `event_type` | varchar(50) | no | none | `new_video` or `test`. |
| `event_key` | varchar(255) | yes | null | Optional unique key for test/request observability. |
| `status` | varchar(50) | no | `pending` | `pending`, `sent`, `failed`, `skipped`, `invalid_token`. |
| `attempt_count` | integer | no | `0` | Increment per Expo attempt. |
| `last_attempt_at` | timestamptz | yes | null | Last send attempt. |
| `last_success_at` | timestamptz | yes | null | Last successful ticket acceptance. |
| `last_error` | text | yes | null | Client-safe error; no raw tokens/secrets. |
| `expo_ticket_id` | varchar(255) | yes | null | Ticket ID/summary. |
| `expo_status` | varchar(50) | yes | null | Expo ticket status. |
| `expo_response` | json | yes | null | Sanitized provider metadata only. |
| `created_at` | timestamptz | no | now | Timestamp. |
| `updated_at` | timestamptz | no | now | Timestamp. |

Constraints:

- Unique for new-video idempotency: `(notification_delivery_id, installation_id)` where `notification_delivery_id IS NOT NULL`.
- Optional unique for test observability: `(user_id, installation_id, event_key)` where `event_type='test' AND event_key IS NOT NULL`.
- Index: `(user_id, event_type, created_at)`.

Idempotency decisions:

- Registration is idempotent by `(user_id, installation_id)`.
- Unregister is idempotent; unknown installation returns success with `registered=false`.
- New-video push delivery is idempotent by `(notification_delivery_id, installation_id)`.
- Test sends may create `event_type='test'` deliveries. No duplicate guarantee is required beyond request-level best effort.

## Push Trigger Integration

MVP push integrates with `YouTubePollingService.run_poll` only at the new-video branch after both records exist:

1. Canonical `Video` from `_get_or_create_video`.
2. Email/activity `NotificationDelivery` from `_get_or_create_delivery`.

Required behavior:

- Trigger only when a new latest upload differs from `UserChannel.last_seen_video_id`.
- Trigger only for rows selected by `UserChannel.is_monitored == True`.
- Trigger only when global push is enabled.
- Trigger only when the channel effective preference is enabled.
- Trigger only for enabled/registered installations with valid Expo tokens.
- Manual mobile Run Poll and QStash/internal automatic poll trigger identically because both call `YouTubePollingService.run_poll`.
- Do not trigger for baseline establishment.
- Do not trigger for unchanged latest video.
- Do not trigger for unmonitored channels.
- Do not trigger for existing pending/retry email delivery processing.
- Do not trigger for quota/safety block.
- Do not trigger for sync failure, poll failure, channel polling failure, or email delivery failure.
- Push send failure must not change the poll summary from success to failure and must not roll back `Video`, `NotificationDelivery`, or `last_seen_video_id` changes.

Recommended call shape for implementation planning only:

```text
after delivery = _get_or_create_delivery(...):
  attempt_new_video_push(
    session=session,
    user=user,
    channel=channel,
    video=video,
    notification_delivery=delivery,
  )
```

## Expo Push Service Integration

- Backend sends to `EXPO_PUSH_ENDPOINT`, defaulting to `https://exp.host/--/api/v2/push/send`.
- Use existing `httpx` for MVP HTTP calls.
- No Expo provider SDK is mandatory.
- `PUSH_NOTIFICATIONS_ENABLED=false` disables actual provider sends globally at deployment/runtime.
- If disabled, endpoints may still allow registration/settings, but test sends should clearly report disabled behavior and poll sends should record skipped/no-op state if a delivery row is created.
- Backend should treat Expo push tokens as rotatable and update on registration.
- Backend should disable or mark installations when Expo returns invalid token errors such as device not registered.
- Expo receipts are optional for MVP. If implemented, they must not require Celery/Redis/new workers in MVP.

New-video payload example sent to Expo:

```json
{
  "to": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "title": "New video from Example Channel",
  "body": "Example Video",
  "sound": "default",
  "data": {
    "type": "new_video",
    "activity_id": 789,
    "delivery_id": 789,
    "video_id": 456,
    "channel_id": 123,
    "sent_at": "2026-05-08T12:05:00Z"
  }
}
```

Test payload example:

```json
{
  "to": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "title": "YTPipe test notification",
  "body": "Push notifications are connected.",
  "sound": "default",
  "data": {
    "type": "test",
    "sent_at": "2026-05-08T12:15:00Z"
  }
}
```

Payload restrictions:

- No bearer tokens.
- No internal API tokens.
- No provider credentials.
- No raw Expo token in `data`.
- No internal URLs.
- No stack traces or sensitive diagnostics.
- YouTube URL is not required in the payload; Activity can refetch current backend data.

## Mobile Responsibilities

- Request OS notification permission from Settings only; never prompt on first launch.
- Require valid backend config before permission/register/test actions.
- Generate and persist a local UUID `installation_id`.
- Obtain Expo push token after permission is granted.
- Register/update the installation using only `MOBILE_API_BEARER_TOKEN`.
- Retry registration from Settings when permission is granted but backend registration failed.
- Send test notification from Settings after registration.
- On Clear Config, attempt unregister best-effort before deleting backend config/token locally.
- On permission revoked/denied after previous registration, attempt unregister best-effort.
- Show Settings states for backend config, OS permission, installation ID/token availability, backend registration, global enabled, delivery diagnostics, retry, and test.
- Show channel push badge/status in channel list when known.
- Show the per-channel push switch in Channel Detail for monitored channels.
- Hide or disable per-channel push controls for unmonitored channels.
- On notification tap, open Activity and refetch. Payload IDs may be used only as optional context.
- Do not log or display raw Expo tokens except masked technical diagnostics.

## Lifecycle Flows

### Enable Push From Settings

1. User has valid `apiBaseUrl` and `mobileApiToken`.
2. User opens Settings and explicitly chooses notification setup.
3. Mobile requests OS notification permission.
4. If granted, mobile obtains Expo push token.
5. Mobile creates or reuses persistent `installation_id`.
6. Mobile calls `POST /internal/mobile-push/register`.
7. Mobile calls `GET /internal/mobile-push/status?installation_id=<uuid>`.
8. User enables global push through `PATCH /internal/mobile-push/settings` if desired.
9. Backend effective defaults enable currently monitored channels unless explicitly disabled.

### Registration Retry

1. OS permission is granted and Expo token is available.
2. Backend registration fails or times out.
3. Mobile shows not-registered/retry state.
4. Retry calls `POST /internal/mobile-push/register` with the same `installation_id`.
5. Backend upserts by `(user_id, installation_id)`.

### Clear Config

1. User chooses Clear Config.
2. Mobile attempts `DELETE /internal/mobile-push/installations/{installation_id}` with the current config/token.
3. Mobile clears local backend config/token even if unregister fails.
4. Mobile clears or resets local push setup state as appropriate.
5. If unregister failed, mobile may show a transient warning but must not block config clearing.

### Permission Revoked

1. Mobile detects notification permission revoked or unavailable.
2. Mobile shows push disabled/unavailable.
3. Mobile attempts unregister best-effort if backend config and installation ID are still available.
4. Backend disables the installation if found.

### Channel Preference Toggle

1. Mobile reads `GET /internal/mobile-push/channel-preferences?monitoring=monitored`.
2. Channel list shows badge/status.
3. Channel Detail shows switch only for monitored channels.
4. Toggle calls `PATCH /internal/mobile-push/channels/{channel_id}`.
5. Backend returns `409` if the channel is not monitored.

### New Video Push

1. Automatic poll or manual Run Poll enters `YouTubePollingService.run_poll`.
2. Backend processes pending/retry email deliveries without push side effects.
3. Backend polls monitored channels.
4. Baseline and unchanged latest upload produce no push.
5. New video branch creates/reuses canonical `Video` and email/activity `NotificationDelivery`.
6. Backend evaluates global setting, channel effective preference, and enabled installations.
7. Backend creates/uses `mobile_push_deliveries` rows for idempotency.
8. Backend sends synchronously to Expo best-effort.
9. Backend records success/failure and continues the poll regardless of push failure.
10. User taps notification; mobile opens Activity and refetches existing `/internal/activity`.

## Error Handling And Observability

Expected API errors:

| Status | Applies To | Meaning |
| --- | --- | --- |
| `401` | All push endpoints | Missing/invalid mobile bearer token. |
| `404` | Channel preference update | Unknown channel. |
| `404` | Status/test where applicable | Unknown installation if endpoint chooses not to return unregistered state. |
| `409` | Settings/register/test/channel pref | Missing backend user/config, push disabled for test, installation not registered, or channel not monitored/not push-eligible. |
| `422` | All push endpoints | Invalid DTO, UUID, query enum, pagination, or path parameter. |
| `502` | Test endpoint only | Expo/provider send failed and no queued/success state exists. |

Stable behavior choices:

- Unregister unknown installation returns success with `registered=false`.
- `PATCH /internal/mobile-push/channels/{channel_id}` for an unmonitored channel returns `409` with a clear `detail`.
- Poll send failures are recorded in `mobile_push_deliveries` and installation summary fields; they do not affect poll HTTP response.
- Existing `/internal/activity` remains unchanged for MVP.
- Existing `/status` remains unchanged for MVP; optional high-level push summary is post-MVP.

Observability minimum:

- Store attempt count, last attempt, success, failure, Expo ticket/status, and sanitized error in `mobile_push_deliveries`.
- Mirror latest installation-level delivery state on `mobile_push_installations` for Settings status.
- Do not store unsanitized provider responses if they include sensitive data.
- Do not log raw Expo tokens.

## Security Requirements

- Mobile-push endpoints must use `require_mobile_bearer_token` only.
- Mobile never uses, stores, logs, or displays `INTERNAL_API_BEARER_TOKEN`.
- Personal admin/mobile bearer auth is not public multi-user authentication and must not be described as suitable for public app distribution.
- Provider/server credentials stay backend-only.
- No tokens/secrets in push payload `data`.
- No raw Expo tokens in UI.
- Mask Expo tokens in status responses, for example `ExponentPushToken[abcd…wxyz]`.
- Do not log raw Expo tokens or bearer tokens.
- Error messages must be client-safe and must not include provider credentials, bearer tokens, stack traces, or internal-only URLs.
- The Expo access token, if ever required by project configuration, is backend-only and not required for MVP by default.

## Environment And Deployment Contract

Future settings additions:

| Env var | Default | Required | Notes |
| --- | --- | --- | --- |
| `PUSH_NOTIFICATIONS_ENABLED` | `false` | no | Global runtime flag for actual provider sends. |
| `EXPO_PUSH_ENDPOINT` | `https://exp.host/--/api/v2/push/send` | no | Expo Push API endpoint. |
| `EXPO_PUSH_RECEIPTS_ENABLED` | `false` | no | Optional receipt checking if implemented. |
| `EXPO_PUSH_RECEIPTS_ENDPOINT` | `https://exp.host/--/api/v2/push/getReceipts` | no | Only needed if receipts are implemented. |
| `EXPO_ACCESS_TOKEN` | empty | no | Not required for MVP unless Expo project configuration later requires it. Backend-only if added. |

Deployment requirements:

- Add these variables to `.env.example` and Render configuration in the appropriate backend implementation phase.
- Default local/staging behavior should be safe with provider sends disabled unless explicitly enabled.
- No mobile repo configuration should contain Expo provider server credentials or backend internal tokens.

## Testing And Verification Strategy

Backend verification:

- Migration tests/inspection for all four new tables, FKs, unique constraints, defaults, and nullable rules.
- Auth tests proving every push endpoint accepts `MOBILE_API_BEARER_TOKEN` and rejects missing/wrong/internal-only auth.
- Contract tests for each request/response DTO and validation error.
- Registration upsert/idempotency tests by `(user_id, installation_id)`.
- Unregister idempotency tests, including unknown installation success.
- Settings tests for default disabled state and first global enable behavior.
- Channel preference tests for monitored success, unmonitored `409`, explicit disable preservation, and re-enable inheritance behavior.
- Trigger tests proving push sends only from the new-video branch after `Video` and `NotificationDelivery` exist.
- Negative trigger tests for baseline, unchanged latest video, unmonitored channel, email retry processing, quota block, sync failure, poll failure, and email delivery failure.
- Idempotency tests proving no duplicate new-video sends for the same `(notification_delivery_id, installation_id)`.
- Test endpoint success/failure tests, including `502` provider failure behavior.
- Payload tests proving no bearer tokens, raw Expo tokens, provider credentials, internal tokens, or internal URLs are present.
- Staging validation with at least one Expo device for register, status, test, per-channel preference, manual poll new-video path, tap-to-Activity, Clear Config unregister, and permission-revoked unregister.

Mobile verification:

- Settings-only permission request.
- Register/retry/test states.
- Clear Config unregister best effort.
- Permission revoked unregister best effort.
- Channel Detail switch only for monitored channels.
- Channel list badge/status.
- Notification tap opens Activity and refetches.

## Implementation Phases

Current repository workflow requires implementation specs per backend phase before execution. Do not implement multiple phases in one uncontrolled pass.

### Backend Phase 11A: Schema, Settings, Service Skeleton

- [ ] Create a phase implementation spec before coding.
- [ ] Add settings/env contract for push flags/endpoints.
- [ ] Add migrations/models for `mobile_push_settings`, `mobile_push_installations`, `mobile_push_channel_preferences`, and `mobile_push_deliveries`.
- [ ] Add service skeleton for registration, settings, preference evaluation, delivery ledger, payload building, and Expo HTTP send abstraction.
- [ ] Verify migrations and model constraints.

### Backend Phase 11B: Endpoints, Status, Preferences, Test

- [ ] Create a phase implementation spec before coding.
- [ ] Add `mobile_push` router under `/internal/mobile-push` and include it in `app/main.py`.
- [ ] Ensure every endpoint uses `require_mobile_bearer_token` only.
- [ ] Implement status, register, unregister, settings, channel preferences read/update, and test endpoint.
- [ ] Preserve existing `/internal/channels`, `/internal/activity`, and `/status` DTOs.
- [ ] Verify auth, DTOs, errors, and masking.

### Backend Phase 11C: Poll Trigger, Send, Idempotency

- [ ] Create a phase implementation spec before coding.
- [ ] Integrate push at the new-video branch after `Video` and `NotificationDelivery` exist.
- [ ] Enforce global, monitoring, channel preference, installation, and token gates.
- [ ] Create/use `mobile_push_deliveries` for idempotency.
- [ ] Send synchronously best-effort via Expo.
- [ ] Record failures without failing or rolling back poll.
- [ ] Verify manual and QStash/automatic poll trigger identically through `run_poll`.

### Mobile Phase D: Expo Notifications Foundation / Settings Registration

- [ ] Use this spec as backend contract reference in the mobile repo.
- [ ] Implement Settings-only permission request, installation ID, Expo token acquisition, register, status, retry, unregister, and test.

### Mobile Phase E: Channel Preference UI

- [ ] Use dedicated channel-preferences endpoint.
- [ ] Add channel list badge/status and Channel Detail switch for monitored channels.

### Mobile Phase F: Tap Handling / Activity Refresh

- [ ] Open Activity from push tap and refetch existing activity list.
- [ ] Treat payload IDs as optional context only.

## Acceptance Criteria

- [ ] This spec is the source of truth for backend mobile-push planning and supersedes the draft.
- [ ] Backend implementation specs exist for Phase 11A, 11B, and 11C before code execution.
- [ ] All mobile-push endpoints use `MOBILE_API_BEARER_TOKEN` via `require_mobile_bearer_token` only.
- [ ] Mobile never receives or uses `INTERNAL_API_BEARER_TOKEN`.
- [ ] Push settings are globally disabled by default.
- [ ] First global enable makes monitored channels effectively push-enabled unless explicitly disabled.
- [ ] Per-channel push is effective only for monitored channels.
- [ ] Unmonitored channels never generate push notifications.
- [ ] Existing `/internal/channels`, `/internal/activity`, and `/status` contracts remain unchanged for MVP.
- [ ] New-video push triggers only after canonical `Video` and email/activity `NotificationDelivery` exist in the polling new-video branch.
- [ ] Baseline, unchanged latest video, email retry processing, quota/safety, sync failure, poll failure, email failure, and unmonitored channels do not trigger push.
- [ ] Manual and QStash/automatic polling trigger push identically when they reach the same new-video branch.
- [ ] Push send failure during polling is recorded but does not fail or roll back the poll.
- [ ] Registration, unregister, and new-video delivery idempotency behave as specified.
- [ ] Payloads and logs contain no bearer tokens, internal tokens, provider credentials, raw Expo tokens, internal URLs, or sensitive diagnostics.
- [ ] Staging validation succeeds with an Expo device for register, test, new-video push, and tap-to-Activity refetch.

## Open Items / Post-MVP

- [ ] Queue/outbox/background processing if reliability requirements exceed synchronous best effort.
- [ ] Expo receipt polling and dashboards.
- [ ] High-level push summary in `/status`.
- [ ] Merging push fields into existing `GET /internal/channels` DTO.
- [ ] Push delivery history screen.
- [ ] Quiet hours or more granular event categories.
- [ ] Native FCM/APNs direct-provider implementation.
- [ ] Public multi-user authentication/authorization model.
- [ ] Push notifications for quota/safety, sync failure, poll failure, or email delivery failure events.
