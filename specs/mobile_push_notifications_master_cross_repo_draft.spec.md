# YTPipe Push Notifications Master Cross-Repo Draft Specification

## Status And Ownership

- [ ] Status: master draft owned in the mobile repo.
- [ ] Purpose: preserve cross-repo product, security, API, and lifecycle decisions for YTPipe mobile push notifications.
- [ ] Backend status: not final until backend repo evidence validates endpoints, schema, trigger integration, jobs, and deployment environment.
- [ ] Implementation guardrail: do not implement mobile push until backend endpoints are implemented or at least contract-stable.
- [ ] Handoff: copy or reference this draft in the backend repo before backend design work starts.

This document supersedes the earlier roadmap-level note for planning detail, while preserving the current rule that push is future scope until backend support exists.

## Context

YTPipe is a single-user personal YouTube subscription notifier with a React Native/Expo mobile app. Existing mobile API contract states:

- Mobile/admin API requests use `MOBILE_API_BEARER_TOKEN`.
- Mobile must never use, store, or expose `INTERNAL_API_BEARER_TOKEN`.
- No push notification endpoints, device-token registration, notification preferences, or push observability exist in the current mobile contract.
- Only explicitly monitored channels are polled, detected, and notified.

This master draft is intentionally detailed enough to carry approved mobile decisions into backend design. The backend repo must create the definitive backend/cross-repo spec after inspecting backend files, schema, migrations, polling code, jobs, and deployment configuration.

## Goals

- [ ] Enable MVP push notifications for new videos detected on monitored channels.
- [ ] Support a manual test notification to the current mobile installation.
- [ ] Use Expo Push Service for MVP.
- [ ] Support multiple mobile devices/installations for the same single-user backend/admin token.
- [ ] Provide global push enable/status in Settings and per-channel push preferences for monitored channels.
- [ ] Keep payloads minimal and free of secrets.
- [ ] Provide enough backend delivery state for mobile Settings to show simple registration/delivery status.
- [ ] Make manual Run Poll impact explicit: new videos found by manual polling may trigger push exactly like automatic polling.

## Non-Goals

- [ ] Quota/safety push notifications.
- [ ] Sync failure push notifications.
- [ ] Poll failure push notifications.
- [ ] Email or delivery error push notifications.
- [ ] Quiet hours.
- [ ] Per-event preference UI beyond new-video/test behavior.
- [ ] Push delivery history screen.
- [ ] Deep links to activity detail or video detail screens.
- [ ] Public multi-user auth.
- [ ] Native FCM/APNs direct-provider implementation.
- [ ] Mobile storage of provider server credentials.

## Product Decisions

### MVP Push Events

- [ ] New video detected for an explicitly monitored channel.
- [ ] Manual test notification.
- [ ] Excluded from MVP: quota/safety, sync failures, poll failures, email/delivery errors.

### Permission UX

- [ ] OS notification permission is requested only from Settings.
- [ ] Permission request is available only after valid backend config exists.
- [ ] No first-launch notification permission prompt.
- [ ] If OS permission is denied/revoked, push is disabled or unavailable in UI.

### Preferences

- [ ] MVP includes per-channel push preferences.
- [ ] Push applies only to explicitly monitored channels.
- [ ] Settings includes a global push enable/status section.
- [ ] At global push enable time, all currently monitored channels become push-enabled by default.
- [ ] Channel list shows push badge/status only, not the primary control.
- [ ] Channel Detail exposes the per-channel push switch for monitored channels.
- [ ] If Monitoring is disabled for a channel, push is disabled or ignored for that channel.
- [ ] Push must never be sent for unmonitored channels.
- [ ] If a channel becomes monitored again later, backend/mobile can apply a default policy; recommended behavior is that monitored channels inherit the global push default unless the user explicitly disables per-channel push afterwards.
- [ ] No quiet hours in MVP.

### Manual Run Poll

- [ ] If manual Run Poll detects a new video and creates the same new-video activity/event as automatic polling, it may trigger push exactly like automatic polling.
- [ ] Mobile UI copy for Run Poll should account for this action impact once push is enabled.

### Clear Config / Unregister

- [ ] On Clear Config, mobile attempts backend unregister best-effort before clearing local backend config/token.
- [ ] Clear Config removes local installation push state as appropriate.
- [ ] Clear Config should stop future push to that installation where possible.
- [ ] Clear Config must still clear local config if unregister fails, but should not claim backend unregister succeeded.

## User Experience

### Settings Push Section

Settings is the primary place to enable and diagnose push:

- Shows backend config validity.
- Shows OS notification permission state: not requested, granted, denied, revoked/unavailable.
- Shows local installation state: installation ID present/missing, Expo push token present/missing.
- Shows backend registration state: registered, not registered, registration failed, retry available.
- Shows global push enabled/disabled status from backend.
- Offers Request Permission only after backend config is valid.
- Offers Register/Retry when permission is granted but backend registration is missing or failed.
- Offers Send Test after token registration, or as retry/diagnostic depending on backend status.
- Shows simple backend delivery state such as last attempt, last success, last error, and Expo ticket/status if available.

If OS permission is granted but backend registration fails, the app must show a `not registered` or retry state and must not claim push is fully enabled.

If OS permission is revoked, the app shows push disabled/unavailable and attempts backend unregister best-effort.

### Channels List

- Shows push badge/status for monitored channels when push status is known.
- Does not make the badge the primary preference control.
- For unmonitored channels, indicates not push-eligible or hides push status.

### Channel Detail

- Shows a push switch only for monitored channels.
- Allows toggling per-channel `push_enabled` for that monitored channel.
- If Monitoring is disabled, push becomes disabled/ignored and the switch is hidden or disabled.

### Notification Tap

- Tapping a new-video push opens Activity.
- Activity performs refetch after navigation.
- If the specific activity item is not available yet, Activity shows normal loading, empty, error, or stale states.
- Deep linking to a future detail screen is out of MVP.

## Security Model

- [ ] All push registration, preference, status, and test endpoints use the mobile bearer token.
- [ ] Mobile uses only `MOBILE_API_BEARER_TOKEN` for backend API calls.
- [ ] Mobile never uses, stores, logs, or exposes `INTERNAL_API_BEARER_TOKEN`.
- [ ] Expo push provider credentials and server-side secrets stay backend-only.
- [ ] Mobile never stores provider server credentials.
- [ ] Device tokens are sensitive-ish identifiers; do not log carelessly and do not expose raw tokens in UI except optional masked technical detail.
- [ ] Notification payloads must not include bearer tokens, internal tokens, provider credentials, internal URLs, or sensitive backend diagnostics.
- [ ] Registration and unregister endpoints must be idempotent.

## Mobile Responsibilities

- [ ] Request OS notification permission from Settings only.
- [ ] Get Expo push token after permission is granted.
- [ ] Generate persistent local `installationId` as a UUID.
- [ ] Store `installationId` locally, separate from the bearer token.
- [ ] Register/update the current installation with backend using the mobile bearer token.
- [ ] Unregister best-effort on Clear Config and permission revoke.
- [ ] Render Push Settings states for permission, backend registration, global enable, retry, and send test.
- [ ] Render channel-level push state: badge/status in Channels list and switch in Channel Detail for monitored channels.
- [ ] Handle notification response/tap by navigating to Activity and refetching.
- [ ] Avoid raw token logging or display.

## Backend Responsibilities

- [ ] Create schema/tables for mobile push installations/devices.
- [ ] Create schema/tables for per-channel push preferences.
- [ ] Store delivery state/last status per device/installation.
- [ ] Register/update/unregister the current installation.
- [ ] Store Expo token and handle token lifecycle; treat Expo push token as rotatable.
- [ ] Treat `installationId` as stable installation identity.
- [ ] Enforce push only for monitored channels and enabled preferences.
- [ ] Trigger push on new video detection regardless of manual or automatic poll source if new activity is created.
- [ ] Send via Expo Push Service.
- [ ] Handle invalid Expo tokens and disabled devices.
- [ ] Provide current push status/preferences endpoints for mobile.
- [ ] Keep provider credentials and operational secrets backend-only.

## Draft Backend API Contract

This API contract is a draft. Backend repo evidence must validate paths, DTO fields, table availability, handler structure, auth middleware, and job/poll integration before finalizing.

All endpoints below are protected by:

```http
Authorization: Bearer <MOBILE_API_BEARER_TOKEN>
```

### `GET /internal/mobile-push/status`

Returns current installation/global/device push status and defaults. Mobile should call this for Settings and may refresh it after registration, unregister, permission changes, and test send.

Recommended query parameter:

- `installation_id`: current local installation UUID, if backend needs to disambiguate current device.

Recommended response:

```json
{
  "global": {
    "enabled": true,
    "default_for_monitored_channels": true
  },
  "installation": {
    "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
    "registered": true,
    "enabled": true,
    "platform": "ios",
    "app_version": "1.0.0",
    "build_number": "42",
    "token_masked": "ExponentPushToken[abcd…wxyz]",
    "last_registered_at": "2026-05-08T12:00:00Z",
    "last_unregistered_at": null
  },
  "delivery": {
    "last_attempt_at": "2026-05-08T12:05:00Z",
    "last_success_at": "2026-05-08T12:05:02Z",
    "last_error": null,
    "last_expo_ticket": "ticket-id-or-summary",
    "last_expo_status": "ok",
    "last_receipt_checked_at": null
  }
}
```

If no current installation is registered, return `registered: false` and avoid exposing another device's raw token.

### `POST /internal/mobile-push/register`

Registers or updates the current installation. Must be idempotent by `installationId`.

Recommended request:

```json
{
  "installationId": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "expoPushToken": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "platform": "ios",
  "appVersion": "1.0.0",
  "buildNumber": "42",
  "deviceName": "Owner iPhone"
}
```

Recommended response:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "registered": true,
  "enabled": true,
  "global_enabled": true,
  "last_registered_at": "2026-05-08T12:00:00Z"
}
```

### `DELETE /internal/mobile-push/register/{installation_id}` Or `DELETE /internal/mobile-push/current`

Unregisters the current installation. Backend should choose one final shape. Endpoint must be idempotent and use mobile bearer auth. If using `/current`, request body or query may include `installationId`.

Recommended response:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "registered": false,
  "unregistered_at": "2026-05-08T12:10:00Z"
}
```

### `PATCH /internal/mobile-push/settings`

Updates global push enablement.

Recommended request:

```json
{
  "enabled": true
}
```

Recommended behavior:

- On first global enable, backend defaults all currently monitored channels to push-enabled.
- If disabled globally, no new-video push is sent even if per-channel flags remain enabled.
- Re-enable behavior should preserve explicit per-channel disables where possible.

Recommended response:

```json
{
  "enabled": true,
  "default_for_monitored_channels": true,
  "monitored_channels_push_enabled_count": 12
}
```

### Channel Preference Read Options

Backend should choose one final contract:

Option A: add push fields to existing `GET /internal/channels` channel DTO:

```json
{
  "channel_id": 123,
  "title": "Example Channel",
  "is_monitored": true,
  "push_eligible": true,
  "push_enabled": true
}
```

Option B: provide a separate endpoint:

```http
GET /internal/mobile-push/channel-preferences?monitoring=monitored
```

Recommended response:

```json
{
  "channels": [
    {
      "channel_id": 123,
      "is_monitored": true,
      "push_eligible": true,
      "push_enabled": true,
      "updated_at": "2026-05-08T12:00:00Z"
    }
  ]
}
```

### `PATCH /internal/mobile-push/channels/{channel_id}`

Updates push preference for one monitored channel.

Recommended request:

```json
{
  "push_enabled": false
}
```

Recommended response:

```json
{
  "channel_id": 123,
  "is_monitored": true,
  "push_eligible": true,
  "push_enabled": false,
  "updated_at": "2026-05-08T12:00:00Z"
}
```

If the channel is unmonitored, backend should reject with a clear client-safe error or return `push_eligible: false` and no effective enablement. Final behavior must be contract-stable before mobile implementation.

### `POST /internal/mobile-push/test`

Sends a test push to the current installation.

Recommended request:

```json
{
  "installationId": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21"
}
```

Recommended response:

```json
{
  "sent": true,
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "last_attempt_at": "2026-05-08T12:15:00Z",
  "expo_status": "ok",
  "message": "Test notification queued"
}
```

## Data Model Draft

Backend names and migrations must be finalized in the backend repo. Draft entities:

### Mobile Push Installations / Devices

- `installation_id`: stable UUID generated by mobile.
- `expo_push_token`: current Expo token, rotatable.
- `platform`: `ios`, `android`, or `unknown`.
- `app_version`: optional.
- `build_number`: optional.
- `device_label`: optional user/device label.
- `enabled`: backend device enabled flag.
- `registered_at`, `updated_at`, `unregistered_at`.
- `last_seen_at`.
- Delivery state fields: `last_attempt_at`, `last_success_at`, `last_error`, `last_expo_ticket/status`, optional `last_receipt_checked_at`.

### Global Push Settings

- `enabled`: global push enable flag.
- `default_for_monitored_channels`: default policy for monitored channels.
- `first_enabled_at`, `updated_at`.

### Per-Channel Push Preferences

- `channel_id`.
- `push_enabled`.
- `explicitly_set`: recommended to distinguish user override from inherited default.
- `updated_at`.

### Optional Outbox / Delivery Log

- Optional for MVP unless backend chooses it for reliability.
- Useful fields: `event_id`, `activity_id`, `channel_id`, `video_id`, `installation_id`, `status`, `attempt_count`, `last_error`, timestamps.
- Full delivery history UI is post-MVP.

## Push Trigger Rules

- [ ] Trigger only on new-video detection for monitored channels.
- [ ] Trigger only when global push is enabled.
- [ ] Trigger only when per-channel push is enabled/effective.
- [ ] Trigger only for registered/enabled installations with valid Expo tokens.
- [ ] Manual Run Poll and automatic polling use the same trigger behavior when a new activity/event is created.
- [ ] Do not trigger for quota/safety, sync failure, poll failure, or email/delivery failure events in MVP.
- [ ] Do not trigger for unmonitored channels, even if stale preferences exist.
- [ ] Backend should avoid duplicate sends for the same event/device through idempotency, event IDs, or outbox constraints.

## Payload Contract

Visible notification:

- Title/body includes channel name and video title.
- Copy should be concise and safe for lock screens.

Recommended Expo payload data:

```json
{
  "type": "new_video",
  "activity_id": 789,
  "video_id": 456,
  "channel_id": 123,
  "sent_at": "2026-05-08T12:05:00Z",
  "event_id": "optional-idempotency-or-observability-id"
}
```

Payload restrictions:

- No bearer tokens.
- No internal tokens.
- No provider credentials.
- No internal URLs.
- No sensitive backend diagnostics.
- YouTube URL is not required in payload; Activity already validates backend links if used.

## Expo Push Service Integration

- [ ] MVP provider is Expo Push Service.
- [ ] Mobile obtains an Expo push token and sends it to backend registration.
- [ ] Backend sends to Expo push endpoint.
- [ ] Backend stores and rotates Expo push tokens as installation records update.
- [ ] Backend handles invalid Expo tokens by disabling or marking affected installations.
- [ ] Backend may check Expo receipts after tickets are returned; timing is an open backend decision.
- [ ] Mobile never stores Expo provider server credentials.

## Lifecycle Flows

### Enable Push From Settings

1. User has valid backend `apiBaseUrl` and `mobileApiToken`.
2. User opens Settings Push section.
3. Mobile requests OS notification permission only after user action.
4. If granted, mobile obtains Expo push token.
5. Mobile creates or reuses persistent `installationId`.
6. Mobile calls backend register endpoint.
7. Mobile reads status and shows registered/global state.
8. User enables global push if not already enabled.
9. Backend defaults currently monitored channels to push-enabled on first global enable.

### Registration Failure

1. OS permission is granted.
2. Expo token is obtained.
3. Backend registration fails or times out.
4. Mobile shows not registered/retry state.
5. Mobile must not claim push is fully enabled.

### Permission Revoked

1. Mobile detects OS permission is revoked/denied.
2. Mobile shows push disabled/unavailable.
3. Mobile attempts backend unregister best-effort.
4. Backend disables/removes current installation if possible.

### Clear Config

1. User chooses Clear Config.
2. Mobile attempts unregister with current backend config/token and `installationId`.
3. Mobile clears local backend config/token.
4. Mobile removes local installation push state as appropriate.
5. If unregister failed, mobile may show a transient warning but still clears local config.

### New Video Push

1. Backend detects a new video for a monitored channel through automatic poll or manual Run Poll.
2. Backend creates the normal new-video activity/event.
3. Backend evaluates global push, channel preference, channel monitoring, and registered installations.
4. Backend sends notification through Expo Push Service.
5. Backend records last delivery attempt/success/error per installation.
6. User taps notification; mobile opens Activity and refetches.

## Error Handling And Observability

MVP backend minimum per device/installation:

- `last_attempt_at`.
- `last_success_at`.
- `last_error`.
- `last_expo_ticket` or ticket summary if applicable.
- `last_expo_status` if applicable.
- Optional `last_receipt_checked_at` if backend checks receipts.

Mobile Settings minimum:

- Shows registered/not registered.
- Shows global enabled/disabled.
- Shows OS permission state.
- Shows retry affordance for failed registration.
- Shows simple backend delivery status without exposing raw sensitive tokens.

Post-MVP optional:

- Full delivery log.
- Delivery history screen.
- More granular event categories.
- Receipt polling dashboards.

## Existing Mobile/API Contract Changes

- `/internal/channels` channel DTO may need `push_enabled` and `push_eligible`, or a separate channel preferences endpoint must be added.
- `/status` may optionally include push summary/status for Dashboard/Settings if useful.
- `/internal/activity` may remain unchanged for MVP unless push observability per activity is added later.
- Existing auth remains mobile bearer token only.
- Existing rule remains: non-monitored channels are not polled/detected/notified.

## Implementation Phases

### Phase A: Backend Contract/Schema Decision

- [ ] Copy/reference this draft in backend repo.
- [ ] Inspect backend schema, migrations, polling, activity creation, auth middleware, and deployment config.
- [ ] Finalize table names and migration plan.
- [ ] Finalize endpoint paths and response shapes.
- [ ] Decide outbox/queue versus synchronous send.

### Phase B: Backend Endpoints/Status/Preferences/Test

- [ ] Implement register/update/unregister.
- [ ] Implement status endpoint.
- [ ] Implement global settings endpoint.
- [ ] Implement channel preference read/update.
- [ ] Implement test push endpoint.

### Phase C: Backend Trigger/Send On New Video Detection

- [ ] Integrate push trigger at the confirmed new-video activity/event creation point.
- [ ] Apply monitored-channel and preference gating.
- [ ] Send via Expo Push Service.
- [ ] Record delivery state.
- [ ] Handle invalid tokens.

### Phase D: Mobile Expo Notifications Foundation/Settings Registration

- [ ] Add Expo notification permission flow from Settings only.
- [ ] Generate/store `installationId`.
- [ ] Get Expo push token.
- [ ] Register/unregister with backend.
- [ ] Render Settings push states and retry/test actions.

### Phase E: Mobile Channel Preference UI

- [ ] Show push badge/status in Channels list.
- [ ] Add push switch in Channel Detail for monitored channels.
- [ ] Respect unmonitored/not eligible states.

### Phase F: Tap Handling/Activity Refresh

- [ ] Handle notification responses.
- [ ] Navigate to Activity.
- [ ] Refetch Activity and show existing loading/empty/error/stale states.

### Phase G: Tests/Staging Hardening

- [ ] Verify endpoint auth uses mobile bearer token only.
- [ ] Verify payload contains no secrets.
- [ ] Verify manual and automatic poll trigger behavior.
- [ ] Verify Clear Config unregister best-effort.
- [ ] Verify revoked permission unregister best-effort.
- [ ] Verify multi-device behavior.

## Testing And Verification Strategy

- [ ] Unit/contract tests for backend registration idempotency.
- [ ] Unit/contract tests for unregister idempotency.
- [ ] Backend tests for monitored-channel gating.
- [ ] Backend tests for global disabled and per-channel disabled behavior.
- [ ] Backend tests that manual Run Poll and automatic poll share new-video push trigger behavior.
- [ ] Backend tests that excluded MVP events do not push.
- [ ] Backend tests that invalid Expo tokens disable or mark affected installations.
- [ ] Mobile tests for Settings state rendering: no config, permission not requested, granted/not registered, registered, revoked, failed registration.
- [ ] Mobile tests for Channel list badge/status and Channel Detail switch visibility.
- [ ] Mobile tests for notification tap navigation to Activity/refetch.
- [ ] Staging verification with Expo test device and manual test endpoint.
- [ ] Security verification that logs/UI do not expose raw tokens or secrets.

## Acceptance Criteria

### Cross-Repo Readiness

- [ ] Backend repo has a definitive backend/cross-repo push spec derived from this draft and validated against backend evidence.
- [ ] Final backend contract documents endpoint paths, auth, request/response DTOs, errors, and idempotency behavior.
- [ ] Final backend contract documents schema/migrations for installations, preferences, and delivery state.
- [ ] Final backend contract identifies the exact new-video trigger insertion point.
- [ ] Final backend contract decides synchronous send vs queue/outbox.
- [ ] Mobile implementation does not start until endpoints are implemented or contract-stable.

### Final MVP Behavior

- [ ] User can enable push from Settings without a first-launch permission prompt.
- [ ] User can register current installation with backend after granting OS permission.
- [ ] Multiple installations can be registered for the same single-user backend/admin token.
- [ ] Global push enable defaults currently monitored channels to push-enabled on first enable.
- [ ] User can disable/enable push per monitored channel from Channel Detail.
- [ ] Unmonitored channels never produce push notifications.
- [ ] Manual Run Poll and automatic polling both may trigger new-video push when they detect/create a new-video activity.
- [ ] Test notification sends to the current installation.
- [ ] Tapping a new-video push opens Activity and refetches.
- [ ] Clear Config attempts unregister best-effort and clears local push state as appropriate.
- [ ] Permission revoke shows disabled/unavailable and attempts unregister best-effort.
- [ ] Payloads include minimal IDs only and no secrets.
- [ ] Settings shows simple backend registration/delivery state.

## Open Questions For Backend Repo

- [ ] Exact table names/schema and migrations.
- [ ] Where new video detection records are created and the best insertion point for push event enqueue/send.
- [ ] Whether push sending is synchronous during poll or queued/background job.
- [ ] Expo receipts handling timing.
- [ ] Whether to add push fields to `/internal/channels` or provide a separate preference endpoint.
- [ ] How to model default behavior when a channel is unmonitored then monitored again.
- [ ] How Render/cron/QStash environment handles push env vars/secrets.
- [ ] Whether to use an outbox table for reliability.
- [ ] Final error response shapes for unmonitored channel preference updates.
- [ ] Whether `/status` should include a push summary in addition to the dedicated push status endpoint.

## Backend Handoff Notes

- [ ] Copy or reference this mobile draft in the backend repo before backend implementation planning.
- [ ] Backend repo should create the definitive cross-repo/backend push spec after inspecting backend files, schema, jobs, auth middleware, and deployment environment.
- [ ] Backend spec should preserve approved product/security decisions unless backend evidence requires an explicit documented change.
- [ ] Backend spec should mark any changed decisions for mobile review before implementation.
- [ ] Mobile push implementation should wait until backend endpoints are implemented or the final contract is stable enough for parallel work.
