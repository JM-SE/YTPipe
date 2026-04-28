# Deployment Readiness Specification

## Context
This phase prepares the MVP for the approved free-tier deployment model. Authoritative references: `specs/architecture_snapshot.spec.md` and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Define the production environment variable contract for the approved stack and integrations.
- [ ] Capture deployment assumptions for Render, Neon, and Upstash QStash schedules as the confirmed staging scheduler.
- [ ] Use versioned `render.yaml` / Render Blueprint service configuration rather than dashboard-only configuration.
- [ ] Perform first Render deploy as staging/test, not production: `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`.
- [ ] Keep Alembic migrations manual for MVP deployment; do not run migrations automatically in the Render start command.
- [ ] Defer real Resend setup until before production final.
- [ ] Add production-safe configuration checks before unattended operation.
- [ ] Provide a smoke-test checklist for first deploy and post-change verification.
- [ ] Include protected developer API docs verification for staging and production per `specs/implementation/06a_protected_api_docs.spec.md`.
- [ ] Include staging runtime reliability follow-up for Render + Neon stale connection handling per `specs/implementation/06b_database_connection_resilience.spec.md`.
- [ ] Require a packaging/distribution smoke check that confirms the built artifact contains the runtime and migration files needed for operation.
- [ ] Document free-tier operational concerns relevant to the approved MVP.
- [ ] In scope: env contract, deployment assumptions, startup/config validation expectations, smoke-test checklist, operational cautions.
- [ ] Out of scope: alternative hosting targets, infrastructure expansion, new runtime features.

## Technical Approach
Keep deployment aligned with the MVP hosting model: Render app, Neon PostgreSQL, and Upstash QStash schedules triggering `POST /internal/run-poll`. First deploy is staging/test on Render with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`; staging validates app boot, Render, Neon, Google OAuth, `/health`, bearer-protected `/status`, protected endpoints, polling, and fake email delivery. Production final requires `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, `RESEND_API_KEY`, and `RESEND_FROM_EMAIL` after real Resend account/domain/sender verification.

Use a versioned `render.yaml` / Render Blueprint for service shape, plan, build command, start command, non-secret defaults, and expected secret env keys. Secrets and real env values must use `sync: false` or Render secret environment variables; do not commit secrets. Render guidance: build command installs project dependencies, e.g. `pip install -e .`; start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Do not include `alembic upgrade` in the Render start command for MVP.

Alembic migrations are manual. After Render/Neon env is configured, run `python -m alembic upgrade head` with the staging or production `DATABASE_URL` set. Neon `DATABASE_URL` must use the psycopg SQLAlchemy format and SSL where required, typically `postgresql+psycopg://...?...sslmode=require` or the equivalent Neon-provided URL adapted for SQLAlchemy.

Keep `/health` public for uptime checks. Use bearer-protected `/status` for operational validation. In staging and production, FastAPI Swagger UI, OpenAPI JSON, and ReDoc must also require the same internal bearer token; local docs may remain public. QStash should call `POST /internal/run-poll` with the app bearer token forwarded as `Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>` only after staging endpoint validation. Google OAuth redirect URI for Render must be `https://<render-service-host>/auth/callback` and must be added in Google Cloud Console.

Confirmed staging scheduler: use Upstash QStash schedules. The staging QStash token belongs to the US region, so API calls use base URL `https://qstash-us-east-1.upstash.io`. Staging schedule ID is `ytpipe-staging-hourly-poll`, cron is `0 * * * *` UTC, and destination is `https://<render-service-host>/internal/run-poll`. QStash API calls use `Authorization: Bearer <QSTASH_TOKEN>`. The app bearer token is forwarded with `Upstash-Forward-Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>`, which QStash delivers to the app as the normal `Authorization` header. No request body is required; `{}` is acceptable for manual publish or schedule creation.

Validated staging evidence: manual QStash publish returned a `messageId`; schedule creation returned `scheduleId: ytpipe-staging-hourly-poll`; Render logs show the server started and returned 200 for `POST /internal/run-poll`; QStash logs show the delivery; staging `/status` returned `service=ytpipe`, `environment=staging`, `ready=True`, polling `last_success_at=2026-04-28T20:20:25.554663Z`, email `delivered_count=1`, quota `estimated_units_used_today=1`, and channels `monitored_count=1`.

Historical note: cron-job.org was tried for Render Free but is superseded/discarded for this deployment because its 30 second timeout and Render Free cold-start behavior were unreliable. Do not use cron-job.org as the current recommendation. QStash signature verification is not required for MVP because the app still relies on the existing bearer token forwarded by QStash; signature verification may be considered only as future hardening.

Staging runtime reliability follow-up: the first historical cron-job.org call reached `POST /internal/run-poll` on Render but failed before OAuth/YouTube/polling logic on the first database query with Neon/Postgres `AdminShutdown` wrapped as SQLAlchemy `OperationalError`. Treat this as a stale/closed deployed database connection issue and resolve under `specs/implementation/06b_database_connection_resilience.spec.md`, not as scheduler authentication or OAuth scope failure.

Environment variable contract:

| Variable | Purpose |
| --- | --- |
| `APP_NAME` | Service name shown in operational output. |
| `APP_ENV` | Runtime environment; staging first, production final. |
| `APP_SECRET_KEY` | Application signing/secret material; must be non-placeholder. |
| `INTERNAL_API_BEARER_TOKEN` | Bearer token for protected internal endpoints including `/status` and poll triggers. |
| `DATABASE_URL` | Neon PostgreSQL SQLAlchemy URL using psycopg and SSL where required. |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL; on Render use `https://<render-service-host>/auth/callback`. |
| `POLL_QUOTA_DAILY_BUDGET` | Daily YouTube quota budget used by the polling safety guard. |
| `POLL_QUOTA_SAFETY_STOP_ENABLED` | Enables/disables quota safety stop. |
| `EMAIL_DELIVERY_MODE` | `fake` for staging/test; `resend` for production final. |
| `RESEND_API_KEY` | Required only for real Resend delivery. |
| `RESEND_FROM_EMAIL` | Required only for real Resend delivery. |

Staging expectations: `APP_ENV=staging`, `EMAIL_DELIVERY_MODE=fake`, valid Render/Neon/OAuth/bearer settings, no real Resend requirement, and QStash schedule configured after manual validation. Production expectations: `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, valid Resend credentials and sender, non-placeholder secrets, and QStash configured after manual validation.

## Implementation Steps
1. Define the required and optional environment variables for app runtime, database, OAuth, polling security, quota controls, and email delivery.
2. Document the expected Render Blueprint service behavior, Neon connection assumptions, and QStash trigger contract.
3. Specify production-safe config checks for startup, including missing secrets, invalid URLs, and unsafe placeholder values.
4. Add a packaging/distribution smoke check that verifies the built artifact includes the app package, `alembic/`, and `alembic.ini` so runtime startup and migrations are possible from the shipped output.
5. Document the manual migration command `python -m alembic upgrade head` with the target Neon `DATABASE_URL` set.
6. Write a concise smoke-test checklist covering startup, DB migration, auth, import, poll, status, and email delivery.
7. Capture free-tier concerns such as cold starts, historical cron-job.org timeout limits, QStash scheduling, overlap/retry avoidance, quota budget tuning, and provider reliability checks.
8. Track the approved staging database connection resilience follow-up in `06b_database_connection_resilience.spec.md`.
9. End the phase with a local testing handoff that explains which deployment-readiness checks can be exercised locally, includes a short manual checklist, and names any missing deployment prerequisite for the rest.

Staging smoke checklist:

1. Render service boots.
2. `/health` public endpoint works.
3. `/status` without bearer fails.
4. `/status` with bearer works.
5. Swagger UI, OpenAPI JSON, and ReDoc reject missing or wrong bearer tokens.
6. Swagger UI, OpenAPI JSON, and ReDoc work with the correct bearer token.
7. OpenAPI declares HTTP bearer auth and Swagger UI Authorize can call protected endpoints.
8. DB migrations are applied.
9. Google OAuth callback works using the Render redirect URI.
10. Subscription sync protected endpoint works.
11. Channel listing/toggle protected endpoints work.
12. `POST /internal/run-poll` works with fake email.
13. QStash manual publish to `POST /internal/run-poll` returns a `messageId`.
14. QStash schedule exists with ID `ytpipe-staging-hourly-poll`, cron `0 * * * *` UTC, base URL `https://qstash-us-east-1.upstash.io`, destination `https://<render-service-host>/internal/run-poll`, API auth `Authorization: Bearer <QSTASH_TOKEN>`, and forwarded app auth `Upstash-Forward-Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>`.
15. Render and QStash logs show successful scheduled delivery, and `/status` confirms healthy/ready state after the run.
16. cron-job.org is disabled/superseded and retained only as historical context for Render Free timeout/cold-start issues.

## Acceptance Criteria
- [ ] The spec names the required environment variables and their operational purpose.
- [ ] The deployment assumptions explicitly match Render, Neon, and Upstash QStash schedules.
- [ ] Staging operations document QStash schedule `ytpipe-staging-hourly-poll`, cron `0 * * * *` UTC, base URL `https://qstash-us-east-1.upstash.io`, destination `POST /internal/run-poll`, QStash API bearer auth, and forwarded app bearer auth.
- [ ] Render service configuration is specified through versioned `render.yaml` / Render Blueprint, with no committed secrets.
- [ ] First Render deploy is staging/test with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`.
- [ ] Alembic migrations are manual and are not part of the Render start command.
- [ ] Production final requires real Resend configuration: `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, `RESEND_API_KEY`, and `RESEND_FROM_EMAIL`.
- [ ] Production startup is required to fail clearly when critical configuration is missing or unsafe.
- [ ] A packaging/distribution smoke check requires the built artifact to include the app package, `alembic/`, and `alembic.ini`.
- [ ] A smoke-test checklist exists for verifying the deployed MVP end to end.
- [ ] Staging and production smoke checks verify protected Swagger UI, OpenAPI JSON, and ReDoc access while confirming local docs remain public per `06a_protected_api_docs.spec.md`.
- [ ] Staging runtime reliability includes the approved Render + Neon stale connection follow-up per `06b_database_connection_resilience.spec.md`.
- [ ] Free-tier operational concerns are called out without expanding the product scope.
- [ ] Free-tier operational notes explain Render cold starts, why cron-job.org was superseded due to 30s timeout/unreliable wakeup, the confirmed QStash scheduler, the continued need for DB `pool_pre_ping`, and the risk of aggressive retries before poll locking exists.
- [ ] Phase completion includes a required local testing handoff that separates local checks from deployment-only checks and states the exact missing prerequisite for anything not locally testable.
