# YTPipe Mobile Push RN/Expo Backend Handoff Specification

## Status

- Backend mobile push MVP is implemented, reviewed, and committed through backend phases 11A, 11B, and 11C.
- This handoff is for starting a future separate React Native/Expo repository.
- Suggested copy path in the mobile repository: `YTPipeMobile/docs/backend-push-handoff.md`.
- Source of truth for the full existing mobile API contract remains `specs/mobile_api_contract_for_rn.spec.md`.

## Backend Capabilities Now Available

- Mobile push settings, schema, models, and service foundations exist.
- Mobile-push endpoints exist under `/internal/mobile-push/*`.
- Expo test push exists via `POST /internal/mobile-push/test`.
- Polling new-video push integration exists.
- Push delivery is synchronous best-effort in MVP: no workers, queues, outbox worker, Celery, Redis, or receipt polling dashboard.
- Push failures are recorded but do not fail or roll back polling.
- Existing `/status`, `/internal/channels`, `/internal/activity`, and `POST /internal/run-poll` response contracts remain unchanged.

## Required Mobile Assumptions And Config

- RN/Expo app stores backend config as:
  - `apiBaseUrl`
  - `mobileApiToken`
- Every protected mobile/admin request uses:

```http
Authorization: Bearer <MOBILE_API_BEARER_TOKEN>
```

- Mobile must never use, store, display, or log `INTERNAL_API_BEARER_TOKEN`.
- Store `mobileApiToken` in Expo SecureStore or equivalent secure storage; do not hardcode or commit it.
- Generate one UUID `installation_id` once per app installation and persist it locally.
- Obtain the Expo push token through Expo notifications APIs only after the user explicitly requests notification setup from Settings and OS permission is granted.
- For this personal/admin app, backend URL and token may be entered manually in app settings.

## Existing Non-Push Endpoints Still Used By RN

Use `specs/mobile_api_contract_for_rn.spec.md` as the complete contract. Summary:

| Endpoint | Auth | RN use |
| --- | --- | --- |
| `GET /health` | none | Lightweight uptime/connectivity check. Returns `{ "status": "ok" }`. |
| `GET /status` | mobile bearer | Dashboard readiness, sync, polling, email/activity counts, quota, channel counts. Contract unchanged by push MVP. |
| `GET /internal/channels` | mobile bearer | Channel list/search. Query: `monitoring=monitored|unmonitored|all`, `query`, `limit`, `offset`. Contract unchanged; no push fields. |
| `PATCH /internal/channels/{channel_id}/monitoring` | mobile bearer | Enable/disable monitoring. Enabling makes a channel eligible for future baseline/polling only. |
| `POST /internal/subscriptions/sync` | mobile bearer | Manual subscription catalog sync after backend Google OAuth is complete. Does not enable monitoring or notify. |
| `POST /internal/run-poll` | mobile bearer for manual admin; internal token for QStash | Polls monitored channels only. Manual and QStash/internal poll now trigger push identically when new-video conditions are met. Response shape unchanged. |
| `GET /internal/activity` | mobile bearer | Activity/history list. Push taps should open Activity and refetch this endpoint. Contract unchanged. |

Common existing errors: `401` auth, `404` unknown resource, `409` unmet Google/poll prerequisites, `422` validation, `502` upstream/provider failures where applicable.

## Mobile-Push Endpoint Contracts

All endpoints require `Authorization: Bearer <MOBILE_API_BEARER_TOKEN>` and reject missing/wrong/internal-only auth with `401`.

### `GET /internal/mobile-push/status?installation_id=<uuid>`

Returns global push settings, current installation state, and recent delivery diagnostics. Unknown installation returns `200` with `registered:false` rather than another device's data.

Response shape:

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

Key errors: `401`, `422` invalid UUID/query.

### `POST /internal/mobile-push/register`

Registers or updates the current installation. Idempotent by `(user_id, installation_id)`; Expo push tokens are rotatable.

Request fields:

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

Response shape:

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

Key errors: `401`, `422` invalid body.

### `DELETE /internal/mobile-push/installations/{installation_id}`

Idempotently unregisters an installation. Unknown installation IDs return success with `registered:false`.

Response shape:

```json
{
  "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21",
  "registered": false,
  "enabled": false,
  "unregistered_at": "2026-05-08T12:10:00Z"
}
```

Key errors: `401`, `422` invalid UUID.

### `PATCH /internal/mobile-push/settings`

Updates global push settings. Global push is disabled by default. First global enable keeps `default_for_monitored_channels=true`, making monitored channels effectively enabled unless explicitly disabled.

Request fields:

```json
{
  "enabled": true,
  "default_for_monitored_channels": true
}
```

Response shape:

```json
{
  "enabled": true,
  "default_for_monitored_channels": true,
  "first_enabled_at": "2026-05-08T12:00:00Z",
  "updated_at": "2026-05-08T12:00:00Z",
  "monitored_channels_effectively_enabled_count": 12
}
```

Key errors: `401`, `422` invalid body.

### `GET /internal/mobile-push/channel-preferences?monitoring=monitored|all&query=&limit=&offset=`

Reads push preference state without changing `/internal/channels`. Default `monitoring=monitored`; `monitoring=all` may include unmonitored channels for diagnostics/future UI.

Response shape:

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
    }
  ],
  "pagination": { "limit": 50, "offset": 0, "total": 1 }
}
```

Key errors: `401`, `422` invalid enum/pagination/query.

### `PATCH /internal/mobile-push/channels/{channel_id}`

Sets an explicit per-channel push preference. Channel must exist and currently be monitored.

Request fields:

```json
{ "push_enabled": false }
```

Response shape matches one channel from the preference list:

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

Key errors: `401`, `404` unknown channel, `409` existing but unmonitored channel, `422` invalid path/body.

### `POST /internal/mobile-push/test`

Sends one synchronous best-effort test push to the current installation.

Request fields:

```json
{ "installation_id": "b8d2b241-5e24-4e80-9b4d-17c8922ecb21" }
```

Success response shape:

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

Key errors: `401`, `409` push provider disabled, unknown/unregistered/disabled/invalid installation, `422` invalid body, `502` Expo/provider failure.

## Effective Preference Model

Effective push for a channel is:

```text
settings.enabled
AND user_channel.is_monitored
AND (
  explicit preference if explicitly_set
  else settings.default_for_monitored_channels
)
```

- Global push is disabled by default.
- First global enable with default true makes monitored channels effectively enabled unless explicitly disabled.
- Unmonitored channels never push.
- Re-enabled monitoring inherits the global default unless an explicit preference row already exists.
- Explicit preference rows may remain while monitoring is disabled, but they are ignored until the channel is monitored again.

## Push Trigger Semantics Now Implemented

- Push triggers only in the polling new-video branch after `Video` and `NotificationDelivery` exist.
- Manual `POST /internal/run-poll` and QStash/internal poll trigger identically because both use the same polling service.
- No push for baseline establishment, unchanged latest video, pending/retry email processing, quota/safety block, sync/poll/channel failures, unmonitored channels, or email retry work.
- Email delivery failure does not suppress push.
- Push failure does not alter email delivery status and does not fail polling.
- New-video push is idempotent by `(notification_delivery_id, installation_id)`.
- Backend does not duplicate sends for existing sent, terminal, or already-attempted rows.

## Push Payload Contract For RN

New-video Expo payload `data` fields are safe and limited to:

```json
{
  "type": "new_video",
  "activity_id": 789,
  "delivery_id": 789,
  "video_id": 456,
  "channel_id": 123,
  "sent_at": "2026-05-08T12:05:00Z"
}
```

IDs are optional navigation context. RN should tap-open Activity and refetch `GET /internal/activity`; do not rely on deep-link entity screens yet.

Test payload `data`:

```json
{
  "type": "test",
  "sent_at": "2026-05-08T12:15:00Z"
}
```

Payloads must never contain bearer tokens, internal tokens, provider credentials, raw Expo tokens, internal URLs, stack traces, or sensitive diagnostics.

## RN Implementation Phase Index

1. Mobile Phase D: Foundations/settings/secure config, installation ID, Expo permissions from Settings only, register/status/unregister/test.
2. Mobile Phase E: Channel preference UI using dedicated endpoint; list badges; Channel Detail switch only for monitored channels.
3. Mobile Phase F: Notification tap handling opens Activity/refetch; foreground/background notification basics.
4. Optional Mobile Phase G: staging validation and polish.

### Mobile Phase D: Foundations, Settings, Registration, Test

Goal: connect one Expo installation to the backend safely.

Requirements:
- Settings screen for `apiBaseUrl` and `mobileApiToken`.
- Secure storage for token and backend config.
- Persistent generated `installation_id`.
- Notification permission prompt only from Settings.
- Expo push token acquisition only after permission granted.
- Register, status, unregister, and test actions.

Backend endpoints used:
- `GET /health`
- `GET /internal/mobile-push/status?installation_id=<uuid>`
- `POST /internal/mobile-push/register`
- `DELETE /internal/mobile-push/installations/{installation_id}`
- `PATCH /internal/mobile-push/settings`
- `POST /internal/mobile-push/test`

Edge cases/tests:
- Missing config, wrong token `401`, invalid URL, denied/revoked permission.
- Registration retry with same installation ID.
- Clear Config attempts unregister best-effort before local deletion.
- Test endpoint disabled `409`, invalid installation `409`, provider `502`.
- No secrets or raw Expo tokens in logs.

Acceptance criteria:
- User can save backend config securely, request permission from Settings, register, view status, enable global push, send a test push, and unregister/clear config.

### Mobile Phase E: Channel Preference UI

Goal: let the owner understand and edit per-channel push eligibility.

Requirements:
- Use dedicated channel-preferences endpoint, not `/internal/channels`, for push badges/switches.
- Show badges/status in channel list when preference data is loaded.
- Show Channel Detail push switch only for monitored channels.
- Hide or disable push controls for unmonitored channels.

Backend endpoints used:
- `GET /internal/channels`
- `PATCH /internal/channels/{channel_id}/monitoring`
- `GET /internal/mobile-push/channel-preferences?monitoring=monitored|all&query=&limit=&offset=`
- `PATCH /internal/mobile-push/channels/{channel_id}`

Edge cases/tests:
- Global disabled makes `push_enabled=false` effectively.
- Explicit disabled stays disabled after global toggles.
- Unmonitored preference patch returns `409`.
- Unknown channel returns `404`.
- Re-enabled monitoring inherits default unless explicit preference exists.

Acceptance criteria:
- Monitored channels display effective push state and can be explicitly enabled/disabled; unmonitored channels never present an active push switch.

### Mobile Phase F: Notification Handling And Activity Refresh

Goal: make notification receipt/tap useful without adding new backend DTOs.

Requirements:
- Foreground notification handling basics.
- Background/tap response handling basics.
- Tapping `type:"new_video"` opens Activity and refetches `GET /internal/activity`.
- Treat payload IDs as optional context only.
- Tapping `type:"test"` may open Settings or show a confirmation state.

Backend endpoints used:
- `GET /internal/activity`
- `GET /status` as needed after manual poll or app foreground.

Edge cases/tests:
- App cold start from notification.
- App foreground receipt.
- Missing/unknown payload type.
- Activity fetch fails due to stale/wrong token.
- No deep-link dependency on video/channel detail screens.

Acceptance criteria:
- New-video notification taps consistently open Activity and refresh backend data.

### Optional Mobile Phase G: Staging Validation And Polish

Goal: validate end-to-end behavior with staging backend and one real Expo device.

Requirements:
- Run checklist below.
- Polish copy for disabled provider, registration retry, permission denied, and token/auth failures.
- Confirm no secrets in app logs or screenshots.

Backend endpoints used:
- All Phase D/F endpoints plus manual `POST /internal/run-poll`.

Acceptance criteria:
- Staging checklist passes and any contract mismatch is documented before mobile feature expansion.

## RN Fetch Helper Expectations

Use a shared helper that adds bearer auth, handles JSON/non-JSON responses, and redacts secrets from logs/errors.

```ts
type BackendConfig = {
  apiBaseUrl: string;
  mobileApiToken: string;
};

function redact(value: string): string {
  return value.length <= 8 ? "<redacted>" : `${value.slice(0, 4)}…${value.slice(-4)}`;
}

async function apiFetch<T>(config: BackendConfig, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${config.apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.mobileApiToken}`,
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
  const body = text ? safeJsonParse(text) : undefined;

  if (!response.ok) {
    throw new Error(`API ${response.status}: ${JSON.stringify(body ?? { detail: "Request failed" })}`);
  }

  return body as T;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: "Non-JSON response" };
  }
}

// Do not log mobileApiToken, INTERNAL_API_BEARER_TOKEN, EXPO_ACCESS_TOKEN, or raw Expo push tokens.
// If diagnostics are necessary, log only redacted(config.apiBaseUrl) and HTTP status codes.
```

## Backend Environment And Staging Checklist For Mobile Dev

Backend environment/config prerequisites:
- `MOBILE_API_BEARER_TOKEN` configured.
- `PUSH_NOTIFICATIONS_ENABLED=true` for real Expo push tests.
- `EXPO_PUSH_ENDPOINT` defaults to `https://exp.host/--/api/v2/push/send`.
- Optional `EXPO_ACCESS_TOKEN` is backend-only and should never enter the mobile app.
- Google OAuth/subscription sync/poll prerequisites must be complete before expecting catalog or poll behavior.
- At least one channel must be monitored and have baseline established before expecting a new-video push.

Staging validation checklist:
- [ ] Save `apiBaseUrl` and `mobileApiToken` in mobile Settings.
- [ ] `GET /health` succeeds; protected endpoints reject wrong token and accept mobile token.
- [ ] Generate/persist installation ID.
- [ ] Request notification permission from Settings and obtain Expo token.
- [ ] Register installation.
- [ ] Fetch push status and verify masked token.
- [ ] Enable global push.
- [ ] Send test push and receive it on device.
- [ ] Load channel preferences and toggle one monitored channel.
- [ ] Ensure at least one monitored channel has baseline established.
- [ ] Trigger manual poll/new-video path when a genuinely new latest upload is available.
- [ ] Receive new-video push.
- [ ] Tap notification, open Activity, and refetch `/internal/activity`.
- [ ] Unregister/Clear Config and verify status reflects unregistered/disabled state.

## Out Of Scope / Do Not Implement In Mobile MVP

- Public multi-user auth/login.
- Exposing or storing `INTERNAL_API_BEARER_TOKEN`.
- Backend receipt polling dashboards.
- Push delivery history screen.
- Quiet hours.
- Native direct FCM/APNs provider integration.
- Changing backend DTOs or response contracts.
- UI for unapproved backend features.
