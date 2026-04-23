# YouTube Notifier — Functional & Technical Specification (v2)

---

# PART 1 — FUNCTIONAL SPECIFICATION

## 1. Overview

### Purpose
Create a personal system that reliably notifies the user when any subscribed YouTube channel uploads a new video.

### Core Problem
YouTube notifications are unreliable or inconsistent, causing missed content.

### Solution
A custom backend that monitors subscriptions and sends independent notifications via email.

---

## 2. Goals

### Primary Goal
Ensure the user receives exactly one notification per new video from subscribed channels.

### Secondary Goals
- Avoid duplicate notifications
- Minimize missed uploads
- Keep system simple and maintainable

---

## 3. Scope (MVP)

### In Scope
- Single user
- Google OAuth authentication with minimum required auth and read scopes only
- Import YouTube subscriptions
- Internal MVP channel-management endpoints for listing imported channels and enabling or disabling monitoring
- Monitor only channels explicitly enabled by the user
- Polling-based detection via YouTube Data API using each channel's uploads playlist
- Email notifications
- Retry once on failure

### Out of Scope
- UI
- Multi-user support
- Push/webhooks
- Filtering rules
- Multiple notification channels

---

## 4. User

Single technical user (owner of the system).

---

## 5. Core Flows

### 5.1 Authentication
1. User logs in via Google
2. System stores access token and refresh token in a single-account OAuth record
3. System renews the access token automatically when possible
4. Manual re-auth is required only if the refresh token becomes invalid

### 5.2 Subscription Sync
1. Fetch subscriptions
2. Store channels
3. Create or update a subscription catalog with monitoring disabled by default
4. Do not establish per-channel baseline during full catalog sync
5. Do not notify during catalog sync

### 5.3 Channel Monitoring Management
1. User calls internal MVP management endpoints
2. System lists imported channels and current monitoring state
3. User explicitly enables or disables monitoring per channel
4. Enabling monitoring marks the channel eligible for polling
5. Baseline is established when monitoring is enabled and first polled, with no notification for that baseline

### 5.4 Polling Detection
1. External cron triggers polling
2. System checks only explicitly monitored channels sequentially through the YouTube Data API uploads playlist path
3. Detects latest video

### 5.5 Notification
1. New video detected
2. Send email
3. If the initial send fails with a transient error, mark the delivery `pending_retry`
4. Retry once on the next cycle
5. Mark as delivered or failed

---

## 6. Business Rules

- One notification per video
- Imported channels are not monitored by default
- Only explicitly enabled channels are monitored
- Baseline is established when monitoring is enabled and first polled
- Polling interval is configurable
- Polling uses a configurable internal daily quota budget and safety stop before the real YouTube quota limit
- Poll runs are blocked when the internal quota budget is exhausted
- Channels are processed sequentially in MVP
- Channel-level failures do not abort the full poll run
- Retry only once
- No infinite retries

---

## 7. Error Handling (Functional)

- If a channel API call fails → record the channel error and continue the run
- If email fails with a transient error → mark `pending_retry` and retry once on the next cycle
- If retry fails → mark as failed
- Permanent email failures are marked failed without retry

---

## 8. Success Criteria

- User receives notifications consistently
- No duplicate emails
- System runs unattended

---

## 9. Constraints

- Not real-time
- Dependent on YouTube API
- Limited by free-tier infra

---

# PART 2 — TECHNICAL SPECIFICATION

## 10. Architecture Overview

### Components
- FastAPI backend
- PostgreSQL (Neon)
- External cron trigger
- Email provider API (Resend)

### Flow
cron → API → DB → detect → email

---

## 11. Stack

- Python 3
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- External cron (cron-job.org)
- Email API (Resend)

---

## 12. Key Technical Decisions

- Polling over push (simplicity)
- YouTube Data API uploads playlist detection over search-based detection (quota efficiency)
- External cron (free-tier limitation)
- Resend for simpler MVP email integration
- Email API over SMTP
- Single retry strategy on next cycle for transient email failures only

---

## 13. Data Model

### User
- id
- email

### OAuthAccount
- id
- user_id
- provider
- access_token
- refresh_token
- token_expiry

### Channel
- id
- youtube_channel_id

### UserChannel
- user_id
- channel_id
- is_monitored
- last_seen_video_id
- baseline_established_at

### Video
- id
- youtube_video_id
- channel_id
- youtube_video_id is globally unique

### NotificationDelivery
- user_id
- video_id
- status
- unique(user_id, video_id)

### SyncState
- id
- user_id
- process_type
- last_success_at
- last_error_at
- last_error_message
- metadata
- one record per process type with initial types `subscription_sync`, `polling`, and `quota`

---

## 14. Endpoints

### Auth
- GET /auth/google
- GET /auth/callback

### Internal
- POST /internal/run-poll (protected with `Authorization: Bearer <secret>`)
- GET /internal/channels
- PATCH /internal/channels/{channel_id}/monitoring (`{"is_monitored": true|false}`)
- GET /status

---

## 15. Polling Flow (Detailed)

1. Triggered by cron if quota budget remains available
2. Fetch only explicitly monitored channels
3. For each channel:
    - get latest upload from the channel uploads playlist
    - compare with stored sync state
    - process channels sequentially
    - if a channel fails, record the error and continue
4. If no baseline exists:
    - store the current latest visible video as baseline for that monitored channel
    - do not notify
5. If new after baseline:
    - insert video
    - create notification record
    - send email
    - update sync state
6. At the end of the run, mark the run as successful, partial, or failed based on aggregate outcome

---

## 16. Deduplication

Rule:
- `Video.youtube_video_id` is globally unique
- `NotificationDelivery` has unique constraint on `(user_id, video_id)`

Effect:
- prevents duplicate emails

---

## 17. Retry Policy

- 1 retry max
- retry on next cycle
- retry only transient failures: timeout, 5xx, temporary rate limit, network or transport failures
- do not retry permanent failures: invalid sender or recipient, invalid credentials, malformed request, permanent provider rejection
- status flow: initial attempt -> `pending_retry` -> one retry on next cycle -> `failed` if retry fails

---

## 18. Authentication

- Google OAuth
- single account
- minimum required auth and read scopes only
- store access token and refresh token
- auto-refresh access token when possible
- manual re-auth only if refresh token is unusable
- OAuth callback must not synchronously perform full subscription import plus baseline establishment for all channels

---

## 19. Observability

Expose:
- subscription sync: last successful sync, last sync error
- polling: last successful run, last partial/failed run, channels processed in last run, channels with error in last run, last detected video
- email: last send attempt, last successful send, last email failure
- quota: configured daily budget, estimated current usage, whether safety stop is active
- monitored channels count
- imported channels list with per-channel monitoring state through internal MVP endpoints

---

## 20. Deployment (MVP)

- Render (free)
- Neon DB
- cron-job.org

---

## 21. Evolution Path

### Phase 2
- APScheduler (always-on)

### Phase 3
- Homelab deployment

### Phase 4
- UI
- multi-channel notifications

---

## 22. Risks

- API rate limits
- email failures
- cron reliability

---

## 23. Build Order

1. Auth
2. DB schema
3. Subscription catalog sync
4. Channel monitoring management
5. Polling
6. Email
7. Cron

---

## 24. Final Summary

A simple, reliable MVP using standard Python backend practices, optimized for free deployment and future evolution to a more robust system.
