# Database Connection Resilience Specification

## Context
After staging deploy and the first cron-job.org run, `POST /internal/run-poll` reached Render but returned 500. Render logs showed `psycopg.errors.AdminShutdown: terminating connection due to administrator command`, wrapped as SQLAlchemy `OperationalError`, on the first database query (`SELECT users...` via `session.scalar(select(User))`). The failure occurred before OAuth, YouTube, or polling logic, indicating a stale or closed Neon/Postgres connection in Render/free-tier idle behavior rather than cron auth or OAuth failure.

## Requirements
- [ ] Harden the SQLAlchemy engine for Render + Neon serverless/free-tier idle connection behavior.
- [ ] Configure SQLAlchemy engine creation with `pool_pre_ping=True`.
- [ ] Consider `pool_recycle=300` as the MVP default for non-local staging/production runtimes.
- [ ] Do not change API contracts, product behavior, OAuth behavior, polling behavior, or notification behavior.
- [ ] Treat later OAuth or YouTube failures as separate issues if they occur after the database connection is healthy.

## Technical Approach
Update only the SQLAlchemy engine configuration so pooled connections are checked before use and stale/closed connections are replaced automatically. Prefer a minimal configuration change that applies to deployed non-local environments and preserves existing local developer behavior unless a shared setting is simpler and safe.

`pool_pre_ping=True` is approved MVP implementation scope for this issue. `pool_recycle=300` is approved to consider as the default for staging/production to reduce reuse of idle serverless/free-tier connections.

## Implementation Steps
1. Locate SQLAlchemy engine creation for the application database.
2. Add `pool_pre_ping=True` to the engine configuration.
3. Add or evaluate `pool_recycle=300` for non-local deployed environments if compatible with the existing settings model.
4. Preserve all existing endpoint behavior and authentication requirements.
5. Verify the deployed staging runtime with a manual cron retry.

## Acceptance Criteria
- [ ] Manual cron retry of `POST /internal/run-poll` no longer fails due to a stale or closed database connection.
- [ ] `/health` remains public.
- [ ] `/status` still works with required bearer protection.
- [ ] `/internal/run-poll` still works with required bearer protection.
- [ ] Tests and build still pass.
- [ ] Any later OAuth or YouTube API error is triaged separately from this database connection resilience fix.
