# Architecture Snapshot Specification

## Context
Approved MVP architecture for a personal YouTube upload notifier. This snapshot is the implementation baseline and should not be treated as exploratory.

## Requirements
- [ ] Goal: notify the single user exactly once when a subscribed channel uploads a new video.
- [ ] Constraints: no UI, single user, free-tier-friendly hosting, email-only notifications, polling-only detection.
- [ ] Chosen stack: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL on Neon, Render hosting, Resend, and Upstash QStash schedules.
- [ ] Detection uses the YouTube Data API uploads playlist path.
- [ ] OAuth uses minimum required authentication and read scopes only.
- [ ] Subscription sync builds a catalog only; monitoring is opt-in per channel.

## Technical Approach
Major components: FastAPI API, PostgreSQL persistence, Google OAuth token storage, subscription catalog sync flow, internal channel-management endpoints, protected polling endpoint, Resend email delivery, `/status` visibility, and Upstash QStash schedules.

Core runtime flow: authenticate -> persist OAuth tokens -> sync subscription catalog -> manage channel monitoring through internal endpoints -> QStash calls `POST /internal/run-poll` with forwarded bearer auth -> quota gate -> sequentially poll only monitored channels -> establish baseline when missing -> detect new video -> create delivery -> send email or mark retry state -> expose operational status.

Deployment scheduler decision: staging validated QStash schedule `ytpipe-staging-hourly-poll` at `0 * * * *` UTC using base URL `https://qstash-us-east-1.upstash.io` and destination `https://<render-service-host>/internal/run-poll`. cron-job.org is historical/superseded for Render Free because its 30 second timeout and Render Free cold-start behavior were unreliable.

Operational assumptions: channels are processed sequentially in MVP, channel-level failures do not abort the run, quota safety stop can block runs before the real YouTube limit, and retry happens only on the next cycle.

Frozen decisions: no Celery or Redis, no UI, no multi-user support, no extra notification channels, no change from uploads-playlist detection, and no persistence-model changes without approval.

## Implementation Steps
1. Use this snapshot as the approved architecture reference for implementation planning.
2. Keep new implementation details inside the approved stack and flow boundaries.
3. Treat frozen decisions as non-negotiable unless a later spec changes them.

## Acceptance Criteria
- [ ] The architecture snapshot names the approved stack and hosting choices.
- [ ] The runtime flow includes auth, catalog sync, channel monitoring management, polling, detection, delivery, and status visibility.
- [ ] Sequential polling, partial-success handling, and quota gating are explicitly stated.
- [ ] Frozen decisions align with the existing technical spec.
