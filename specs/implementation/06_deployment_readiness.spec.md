# Deployment Readiness Specification

## Context
This phase prepares the MVP for the approved free-tier deployment model. Authoritative references: `specs/architecture_snapshot.spec.md` and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Define the production environment variable contract for the approved stack and integrations.
- [ ] Capture deployment assumptions for Render, Neon, and cron-job.org.
- [ ] Use versioned `render.yaml` / Render Blueprint service configuration rather than dashboard-only configuration.
- [ ] Perform first Render deploy as staging/test, not production: `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`.
- [ ] Keep Alembic migrations manual for MVP deployment; do not run migrations automatically in the Render start command.
- [ ] Defer real Resend setup until before production final.
- [ ] Add production-safe configuration checks before unattended operation.
- [ ] Provide a smoke-test checklist for first deploy and post-change verification.
- [ ] Require a packaging/distribution smoke check that confirms the built artifact contains the runtime and migration files needed for operation.
- [ ] Document free-tier operational concerns relevant to the approved MVP.
- [ ] In scope: env contract, deployment assumptions, startup/config validation expectations, smoke-test checklist, operational cautions.
- [ ] Out of scope: alternative hosting targets, infrastructure expansion, new runtime features.

## Technical Approach
Keep deployment aligned with the frozen MVP hosting model: Render app, Neon PostgreSQL, and cron-job.org triggering `POST /internal/run-poll`. First deploy is staging/test on Render with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`; staging validates app boot, Render, Neon, Google OAuth, `/health`, bearer-protected `/status`, protected endpoints, polling, and fake email delivery. Production final requires `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, `RESEND_API_KEY`, and `RESEND_FROM_EMAIL` after real Resend account/domain/sender verification.

Use a versioned `render.yaml` / Render Blueprint for service shape, plan, build command, start command, non-secret defaults, and expected secret env keys. Secrets and real env values must use `sync: false` or Render secret environment variables; do not commit secrets. Render guidance: build command installs project dependencies, e.g. `pip install -e .`; start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Do not include `alembic upgrade` in the Render start command for MVP.

Alembic migrations are manual. After Render/Neon env is configured, run `python -m alembic upgrade head` with the staging or production `DATABASE_URL` set. Neon `DATABASE_URL` must use the psycopg SQLAlchemy format and SSL where required, typically `postgresql+psycopg://...?...sslmode=require` or the equivalent Neon-provided URL adapted for SQLAlchemy.

Keep `/health` public for uptime checks. Use bearer-protected `/status` for operational validation. cron-job.org should eventually call `POST /internal/run-poll` with `Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>` only after staging endpoint validation. Google OAuth redirect URI for Render must be `https://<render-service-host>/auth/callback` and must be added in Google Cloud Console.

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

Staging expectations: `APP_ENV=staging`, `EMAIL_DELIVERY_MODE=fake`, valid Render/Neon/OAuth/bearer settings, no real Resend requirement. Production expectations: `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, valid Resend credentials and sender, non-placeholder secrets, and cron-job.org configured after manual validation.

## Implementation Steps
1. Define the required and optional environment variables for app runtime, database, OAuth, polling security, quota controls, and email delivery.
2. Document the expected Render Blueprint service behavior, Neon connection assumptions, and cron-job.org trigger contract.
3. Specify production-safe config checks for startup, including missing secrets, invalid URLs, and unsafe placeholder values.
4. Add a packaging/distribution smoke check that verifies the built artifact includes the app package, `alembic/`, and `alembic.ini` so runtime startup and migrations are possible from the shipped output.
5. Document the manual migration command `python -m alembic upgrade head` with the target Neon `DATABASE_URL` set.
6. Write a concise smoke-test checklist covering startup, DB migration, auth, import, poll, status, and email delivery.
7. Capture free-tier concerns such as cold starts, cron overlap avoidance, quota budget tuning, and provider reliability checks.
8. End the phase with a local testing handoff that explains which deployment-readiness checks can be exercised locally, includes a short manual checklist, and names any missing deployment prerequisite for the rest.

Staging smoke checklist:

1. Render service boots.
2. `/health` public endpoint works.
3. `/status` without bearer fails.
4. `/status` with bearer works.
5. DB migrations are applied.
6. Google OAuth callback works using the Render redirect URI.
7. Subscription sync protected endpoint works.
8. Channel listing/toggle protected endpoints work.
9. `POST /internal/run-poll` works with fake email.
10. cron-job.org is configured only after manual endpoint validation.

## Acceptance Criteria
- [ ] The spec names the required environment variables and their operational purpose.
- [ ] The deployment assumptions explicitly match Render, Neon, and cron-job.org.
- [ ] Render service configuration is specified through versioned `render.yaml` / Render Blueprint, with no committed secrets.
- [ ] First Render deploy is staging/test with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`.
- [ ] Alembic migrations are manual and are not part of the Render start command.
- [ ] Production final requires real Resend configuration: `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, `RESEND_API_KEY`, and `RESEND_FROM_EMAIL`.
- [ ] Production startup is required to fail clearly when critical configuration is missing or unsafe.
- [ ] A packaging/distribution smoke check requires the built artifact to include the app package, `alembic/`, and `alembic.ini`.
- [ ] A smoke-test checklist exists for verifying the deployed MVP end to end.
- [ ] Free-tier operational concerns are called out without expanding the product scope.
- [ ] Phase completion includes a required local testing handoff that separates local checks from deployment-only checks and states the exact missing prerequisite for anything not locally testable.
