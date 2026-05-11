# Mobile API Contract For React Native Companion App

## Purpose

This document is the standalone backend contract between the YTPipe backend and a future personal/admin React Native/Expo companion app. It is intended to be copied into a future separate mobile repository as `docs/backend-contract.md` without requiring hidden chat context.

YTPipe is a single-user personal YouTube subscription notifier. The backend imports a YouTube subscription catalog, lets the owner explicitly mark channels as monitored, polls only monitored channels, detects new uploads, and records email delivery/activity state.

## Deployment Context

- Backend type: single-user/admin personal API, not a public multi-user app backend.
- Current staging deployment: Render app + Neon PostgreSQL + Upstash QStash scheduler.
- Current staging delivery mode: fake email delivery is acceptable for staging/demo.
- Real email via Resend is deferred because the owner does not currently want to buy/configure a production sending domain.
- QStash/internal automation uses `INTERNAL_API_BEARER_TOKEN`.
- Mobile/admin API access uses `MOBILE_API_BEARER_TOKEN`.
- The mobile app must never use the internal QStash token.

## Authentication Contract

Mobile/admin requests must send:

```http
Authorization: Bearer <MOBILE_API_BEARER_TOKEN>
```

The future mobile app must not hardcode the token. It should collect these values in a settings/config screen:

- `apiBaseUrl`
- `mobileApiToken`

The token should be stored in secure on-device storage, such as Expo SecureStore. Rotating `MOBILE_API_BEARER_TOKEN` in Render invalidates old app configuration and requires updating the mobile app settings.

QStash/internal automation uses `INTERNAL_API_BEARER_TOKEN`; do not expose or store that token in the mobile app.

### Swagger/OpenAPI Behavior

- Local development docs are public.
- Staging/production docs are bearer-protected.
- In the Swagger Authorize modal, paste only the token value, not `Bearer <token>`.
- Swagger remains a supported manual testing surface for this backend contract.

### Base URL Examples

Use placeholders only; do not commit real tokens.

```text
Local:   http://127.0.0.1:8000
Staging: https://<ytpipe-staging-render-host>
Prod:    https://<ytpipe-production-render-host>
```

Recommended RN config shape:

```ts
type BackendConfig = {
  apiBaseUrl: string;
  mobileApiToken: string;
};
```

Example fetch helper:

```ts
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

  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    throw new Error(`API ${response.status}: ${JSON.stringify(body)}`);
  }

  return response.json() as Promise<T>;
}
```

Example curl:

```bash
curl -H "Authorization: Bearer <MOBILE_API_BEARER_TOKEN>" \
  "https://<render-host>/status"
```

Example PowerShell:

```powershell
$headers = @{ Authorization = "Bearer <MOBILE_API_BEARER_TOKEN>" }
Invoke-RestMethod -Uri "https://<render-host>/status" -Headers $headers
```

## Core Product Rules For Mobile

- Only explicitly monitored channels are polled, detected, and notified.
- Non-monitored channels are catalog entries only. They can be searched, listed, and activated.
- Enabling monitoring does not immediately mean old videos should notify; it only makes the channel eligible for the normal polling/baseline workflow.
- No mobile OAuth/login exists yet.
- No multi-user auth exists yet.
- No public app auth exists yet.
- No push notifications exist yet.

## Endpoint Contract

### 1. `GET /health`

Public uptime check. No authentication required.

Recommended RN use: lightweight connectivity check only; use `/status` for operational dashboard data.

Response example:

```json
{
  "status": "ok"
}
```

### 2. `GET /status`

Protected by mobile/admin bearer token.

Recommended RN use: dashboard/home screen.

Response shape:

```json
{
  "service": "ytpipe",
  "environment": "staging",
  "ready": true,
  "subscription_sync": {
    "last_success_at": "2026-04-28T20:00:00Z",
    "last_error_at": null,
    "last_error_message": null,
    "metadata": {}
  },
  "polling": {
    "last_success_at": "2026-04-28T20:20:25.554663Z",
    "last_error_at": null,
    "last_error_message": null,
    "last_run": {
      "run_outcome": "success",
      "channels_processed": 1,
      "channels_failed": 0,
      "baselines_established": 0,
      "new_videos_detected": 1,
      "quota_blocked": false,
      "channel_errors": []
    }
  },
  "email": {
    "last_attempt_at": "2026-04-28T20:20:25.554663Z",
    "last_success_at": "2026-04-28T20:20:25.554663Z",
    "last_failure_at": null,
    "last_error": null,
    "pending_count": 0,
    "pending_retry_count": 0,
    "delivered_count": 1,
    "failed_count": 0
  },
  "quota": {
    "daily_quota_budget": 500,
    "estimated_units_used_today": 1,
    "last_run_estimated_units": 1,
    "safety_stop_active": false,
    "safety_stop_enabled": true,
    "safety_stop_triggered_at": null
  },
  "channels": {
    "imported_count": 25,
    "monitored_count": 1
  }
}
```

Important client behavior:

- Treat `ready=false` as degraded/not ready.
- Display `polling.last_run` and `email` summaries separately.
- Email fake mode can still show delivered counts in staging/demo.

### 3. `GET /internal/channels`

Protected by mobile/admin bearer token.

Recommended RN use: channels screen and channel activation/search.

Query parameters:

| Param | Values | Default | Notes |
| --- | --- | --- | --- |
| `monitoring` | `monitored`, `unmonitored`, `all` | `monitored` | Default opens app to active set. |
| `query` | text | none | Optional channel title/name search. |
| `limit` | integer `1..200` | `50` | Page size. |
| `offset` | integer `>=0` | `0` | Page offset. |

Response shape:

```json
{
  "channels": [
    {
      "channel_id": 123,
      "youtube_channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
      "title": "Example Channel",
      "is_monitored": true,
      "last_seen_video_id": "abc123",
      "baseline_established_at": "2026-04-28T19:30:00Z",
      "latest_detected_video": {
        "video_id": 456,
        "youtube_video_id": "abc123",
        "title": "Example Video",
        "youtube_url": "https://www.youtube.com/watch?v=abc123",
        "published_at": "2026-04-28T19:00:00Z"
      }
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "total": 1
  }
}
```

Notes:

- `latest_detected_video` may be `null` when no stored detected video exists.
- Non-monitored channels can appear with `monitoring=unmonitored` or `monitoring=all` and can be activated.
- Non-monitored channels are not polled/detected/notified until monitoring is enabled.

Example:

```text
GET /internal/channels?monitoring=unmonitored&query=music&limit=25&offset=0
```

### 4. `PATCH /internal/channels/{channel_id}/monitoring`

Protected by mobile/admin bearer token.

Recommended RN use: toggle channel monitoring from the channels screen.

Request body:

```json
{
  "is_monitored": true
}
```

Response example:

```json
{
  "channel_id": 123,
  "is_monitored": true,
  "last_seen_video_id": "abc123",
  "baseline_established_at": "2026-04-28T19:30:00Z"
}
```

Rules:

- Enabling monitoring only makes the channel eligible for future polling.
- Baseline fields should be preserved according to backend polling semantics.
- Disabling monitoring removes the channel from future polling/detection/notification.

### 5. `POST /internal/subscriptions/sync`

Protected by mobile/admin bearer token.

Recommended RN use: manual action button to refresh/import the YouTube subscription catalog.

Behavior:

- Imports/updates the YouTube subscription catalog.
- Does not enable monitoring automatically.
- Does not establish polling baselines for every channel.
- Does not notify.
- Requires prior Google OAuth authorization completed in the backend.

Response shape is implementation-specific aggregate sync output, but should include enough summary data for RN to display success/failure counts.

Possible response example:

```json
{
  "status": "success",
  "channels_imported": 25,
  "channels_created": 2,
  "channels_updated": 23
}
```

### 6. `POST /internal/run-poll`

Protected by mobile/admin bearer token for manual admin use. QStash uses the internal token separately.

Recommended RN use: manual action button for controlled polling.

Behavior:

- Polls explicitly monitored channels only.
- Establishes baseline for monitored channels without baseline.
- Detects new videos only for monitored channels.
- Creates video/activity/delivery records only for monitored-channel workflow.
- Returns aggregate response only, not per-channel detail.

Response shape:

```json
{
  "run_outcome": "success",
  "channels_processed": 1,
  "channels_failed": 0,
  "baselines_established": 0,
  "new_videos_detected": 1,
  "quota_blocked": false
}
```

Client notes:

- After a manual poll, refresh `/status`, `/internal/channels`, and `/internal/activity`.
- If `quota_blocked=true`, show a quota/safety-stop message rather than retrying aggressively.

### 7. `GET /internal/activity`

Protected by mobile/admin bearer token.

Recommended RN use: activity/history screen.

Query parameters:

| Param | Values | Default | Notes |
| --- | --- | --- | --- |
| `status` | `all`, `pending`, `delivered`, `pending_retry`, `failed` | `all` | Filters by delivery status. |
| `limit` | integer `1..200` | `50` | Page size. |
| `offset` | integer `>=0` | `0` | Page offset. |

Response shape:

```json
{
  "items": [
    {
      "activity_id": 789,
      "delivery_id": 789,
      "video_id": 456,
      "youtube_video_id": "abc123",
      "video_title": "Example Video",
      "youtube_url": "https://www.youtube.com/watch?v=abc123",
      "channel_id": 123,
      "channel_title": "Example Channel",
      "delivery_status": "delivered",
      "published_at": "2026-04-28T19:00:00Z",
      "detected_at": "2026-04-28T20:20:25.554663Z",
      "last_attempt_at": "2026-04-28T20:20:25.554663Z",
      "last_error": null
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "total": 1
  }
}
```

Notes:

- `last_error` is mainly relevant for `pending_retry` and `failed` deliveries.
- Activity is read-only and must not trigger polling, retries, email sends, or YouTube API calls.

## Error Handling Contract

Clients should expect standard JSON error responses, typically using FastAPI-style `detail` fields.

| Status | Meaning | RN behavior |
| --- | --- | --- |
| `401` | Missing or invalid bearer token. | Prompt user to verify API base URL/token settings. |
| `404` | Unknown channel on toggle or unknown resource. | Refresh channel list and show not found message. |
| `409` | Prerequisite not met, such as Google OAuth not completed before sync/poll. | Show setup-required message. |
| `422` | Validation error for query/body/path parameters. | Highlight invalid input or reset bad filters. |
| `502` | Upstream/polling/sync/provider error. | Show operational error and suggest checking `/status`. |

Example validation error shape:

```json
{
  "detail": [
    {
      "loc": ["query", "limit"],
      "msg": "Input should be less than or equal to 200",
      "type": "less_than_equal"
    }
  ]
}
```

## Recommended RN Screens And Endpoint Usage

### Dashboard

- Use `GET /status`.
- Show readiness, polling last run, email delivery counts, quota status, and monitored/imported channel counts.

### Channels

- Use `GET /internal/channels?monitoring=monitored` for default active view.
- Use `GET /internal/channels?monitoring=unmonitored&query=<text>` for finding channels to activate.
- Use `PATCH /internal/channels/{channel_id}/monitoring` for toggles.

### Manual Actions

- Use `POST /internal/subscriptions/sync` for catalog refresh.
- Use `POST /internal/run-poll` for controlled manual polling.
- Refresh dashboard/activity after actions.

### Activity

- Use `GET /internal/activity?status=all` for recent activity.
- Use status filters for troubleshooting failed or pending deliveries.

## Security Notes

- Store `mobileApiToken` in Expo SecureStore or equivalent secure storage.
- Never commit or hardcode real tokens.
- This is a staging/personal admin model only.
- Rotating `MOBILE_API_BEARER_TOKEN` in Render invalidates old mobile app settings.
- Do not ship this as a public multi-user auth model.
- Do not expose `INTERNAL_API_BEARER_TOKEN` to the mobile app.

## Out Of Scope / Future Work

- Real mobile login/OAuth.
- Public multi-user release.
- Push notifications.
- Resend production/domain setup.
- QStash signature verification.
- Poll locks/concurrency guards.
- Public app authentication/authorization model.

## Manual Backend Validation Checklist Before RN Integration

- [ ] `/health` works without auth.
- [ ] `/status` rejects missing/wrong token and works with `MOBILE_API_BEARER_TOKEN`.
- [ ] Swagger/OpenAPI behavior matches environment: local public, staging/production protected.
- [ ] `GET /internal/channels` works with default `monitoring=monitored`.
- [ ] `GET /internal/channels?monitoring=unmonitored&query=<text>` can find catalog entries.
- [ ] `PATCH /internal/channels/{channel_id}/monitoring` toggles monitoring.
- [ ] `POST /internal/subscriptions/sync` works after backend Google OAuth is complete.
- [ ] `POST /internal/run-poll` works manually and returns aggregate response.
- [ ] `GET /internal/activity` returns paginated activity with expected status filters.
- [ ] Non-monitored channels are not polled/detected/notified.
- [ ] QStash continues using `INTERNAL_API_BEARER_TOKEN`, not the mobile token.

## Suggested First RN Phases

1. Foundations + config storage: create settings screen for `apiBaseUrl` and `mobileApiToken`; store token securely.
2. API client + auth header: implement shared fetch/client wrapper and error handling.
3. Dashboard: render `/status` readiness, polling, email, quota, and channel counts.
4. Channels list/toggle: implement monitored default view, search unmonitored catalog, and monitoring toggle.
5. Activity screen: implement paginated `/internal/activity` with status filters.

## Contract Stability Notes

This document describes the intended backend contract for the future RN companion app. If backend endpoints or response shapes change, update this contract first, then update the mobile app against the revised contract.
