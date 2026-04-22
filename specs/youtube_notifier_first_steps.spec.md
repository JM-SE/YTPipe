# YouTube Notifier — First Steps Before Implementation

## Purpose
This document defines the immediate steps to complete before handing the project to an AI coding agent or starting implementation.

The goal is to reduce ambiguity, prevent premature coding, and ensure the first implementation starts from clear technical constraints rather than assumptions.

---

## 1. Validate and Freeze the MVP Scope

Before implementation begins, confirm that the MVP scope is fixed to the following:

- single-user system
- no UI
- email as the only notification channel
- polling as the only detection strategy for MVP, using the YouTube Data API uploads playlist path
- external cron triggering the polling endpoint
- all imported subscriptions monitored by default
- one retry for transient failed email delivery only
- free-tier-friendly deployment
- initial sync establishes a baseline only and does not backfill notifications

### Expected output
A short explicit confirmation that the above scope is the current MVP contract.

---

## 2. Freeze Core Architecture Decisions

Before coding, the system must have a stable technical baseline.

The implementation should assume:

- backend framework: FastAPI
- language: Python
- database: PostgreSQL
- ORM: SQLAlchemy
- migrations: Alembic
- hosting: Render free tier
- database hosting: Neon free tier
- scheduler model for MVP: external cron
- email provider: Resend

### Important note
The agent should not replace these decisions with alternative technologies unless explicitly instructed.

### Expected output
A short architecture decision record summarizing the approved stack.

---

## 3. Freeze the Initial Email Provider

The MVP email provider is frozen before implementation starts.

### Final decision
- Resend is the only email provider for MVP
- it is chosen for simpler MVP integration

### Expected output
The docs should reflect Resend as the default and only MVP email service.

---

## 4. Define the Polling Contract

Before the polling logic is implemented, define what the polling job is expected to do.

The contract should clarify:

- what endpoint triggers polling
- how the endpoint is protected
- what happens when the polling endpoint is called
- whether channels are processed sequentially or in batches
- how the system determines whether a video is new
- what happens when detection succeeds
- what happens when detection fails
- how quota budget and safety stop prevent over-polling

### Minimum assumptions
- endpoint: `POST /internal/run-poll`
- protected with `Authorization: Bearer <secret>`
- processes all monitored channels for the single user
- processes channels sequentially in MVP
- compares current latest upload from the channel uploads playlist against stored state
- creates a notification attempt only when a new video is detected
- records per-channel failures and allows the run to complete with partial success when needed
- uses a configurable polling interval, configurable daily quota budget, and a configurable preventive stop before the real YouTube quota limit
- blocks additional poll runs when the internal quota budget is exhausted

### Expected output
A short flow definition for the polling execution cycle.

---

## 5. Define the Data Model Before Coding

The database model must be clarified before any endpoint or logic is implemented.

At minimum, the following entities should be confirmed:

- User
- OAuthAccount
- Channel
- UserChannel
- Video
- NotificationDelivery
- SyncState

### For each entity, define
- purpose
- required fields
- unique constraints
- key relationships
- timestamps

### Baseline model decisions
- `Video.youtube_video_id` is globally unique
- `NotificationDelivery` enforces `unique(user_id, video_id)`
- `SyncState` stores one record per process type, with initial types `subscription_sync`, `polling`, and `quota`

### Important note
The agent should not invent or rename core entities without explicit approval.

### Expected output
A concise data model reference that can be translated directly into SQLAlchemy models.

---

## 6. Define Deduplication Rules

Before implementation, define exactly how duplicate notifications are prevented.

The system should specify:

- what identifier is considered unique for a video
- whether uniqueness is per user, per provider, or global
- what record marks an email as already sent
- what happens if the same video is detected again in a later poll

### Resolved rule
- `Video.youtube_video_id` is globally unique
- a user must receive at most one email notification per video
- `NotificationDelivery` is the record that enforces this through `unique(user_id, video_id)`
- repeated detection of the same stored video must not create another delivery

### Expected output
A simple idempotency rule set that the implementation must respect.

---

## 7. Define Retry Behavior Explicitly

Retry behavior must be documented so the agent does not overbuild it.

The MVP behavior should define:

- one retry maximum
- when the retry happens
- what counts as a retryable failure
- what happens after the retry fails

### Resolved MVP rule
- initial send attempt happens immediately during poll processing
- retry only transient failures: timeout, 5xx, temporary rate limit, network or transport failures
- do not retry permanent failures: invalid sender or recipient, invalid credentials, malformed request, permanent provider rejection
- if the initial retryable failure happens, mark the delivery `pending_retry`
- retry once on the next polling cycle
- if the retry fails, mark the delivery failed and stop automatic attempts
- if no baseline exists yet for a channel, store the current latest visible video and do not send a notification

### Expected output
A short retry policy that is simple enough for MVP but clear enough to avoid ambiguity.

---

## 8. Define OAuth Scope and Auth Flow Boundaries

Before implementation, the authentication flow must be narrowed down.

Clarify:

- what Google/YouTube scopes are needed
- that refresh tokens are expected and stored
- whether only one Google account is supported in MVP
- what happens when auth expires
- whether re-auth is manual

### MVP assumption
Single Google account. Use only the minimum required authentication and read scopes, store access token and refresh token, auto-renew the access token, and require manual re-auth only if the refresh token becomes invalid or unusable.

### Expected output
A short auth flow note with scope and token handling boundaries.

---

## 9. Define Operational Visibility for MVP

Even without a UI, the system needs a minimal observability contract.

Define what must be visible through logs or status endpoint:

- subscription sync: last successful sync, last sync error
- polling: last successful run, last partial or failed run, channels processed in last run, channels with error in last run, last detected video
- email: last send attempt, last successful send, last email failure
- quota: configured daily budget, estimated current usage, whether safety stop is active
- general: monitored channels count

### Expected output
A small operational checklist for logs and `/status` endpoint behavior.

---

## 10. Decide What the AI Agent May and May Not Decide

Before handing the documentation to an AI agent, define its autonomy boundaries.

The agent may decide:
- internal folder organization
- helper function decomposition
- naming of non-domain utility modules
- implementation details consistent with the approved architecture

The agent may not decide:
- replacing the stack
- changing the detection strategy
- introducing async infrastructure like Celery or Redis
- adding UI
- adding multi-user support
- introducing additional notification channels
- changing persistence model without approval

### Expected output
A short “agent guardrails” section to include with the technical docs.

---

## 11. Recommended Immediate Deliverables Before Coding

These remain the immediate next documentation outputs before implementation, and they are still pending.

### Deliverable A — Architecture Snapshot
A one-page version of the approved architecture.

### Deliverable B — Data Model Draft
A concise entity and relationship definition.

### Deliverable C — Polling Flow Definition
A step-by-step execution flow for `POST /internal/run-poll`.

### Deliverable D — Retry and Deduplication Rules
A small rules section covering notification safety.

### Deliverable E — Agent Guardrails
A short implementation boundaries note.

---

## 12. Suggested Order of Work Before Handing to the Agent

1. Confirm final MVP scope.
2. Confirm final stack and hosting decisions.
3. Choose email provider.
4. Define data model.
5. Define polling flow.
6. Define deduplication and retry rules.
7. Define auth boundaries.
8. Define status/logging expectations.
9. Write short agent guardrails.
10. Hand functional spec, technical spec, and first-steps document to the agent.

---

## 13. Final Note

The purpose of these first steps is not to slow down implementation.

It is to avoid a common failure mode: starting code generation with incomplete constraints, then spending more time correcting wrong assumptions than building the actual product.

A short preparation phase here should make the implementation phase significantly cleaner, faster, and more deterministic.
