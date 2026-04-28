# Production Finalization Specification

## Context

MVP implementation phases 01-06 are complete for staging. Staging is deployed on Render + Neon with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=fake`. QStash is the confirmed staging scheduler using base URL `https://qstash-us-east-1.upstash.io`, schedule ID `ytpipe-staging-hourly-poll`, cron `0 * * * *`, destination `https://<render-service-host>/internal/run-poll`, and forwarded app bearer auth via `Upstash-Forward-Authorization`. Staging `/status` evidence shows `ready=True`, polling success, fake email delivered, quota usage recorded, and monitored channel count 1.

This follow-up finalizes production readiness after successful staging. It focuses on real Resend setup and production deployment validation, not new product features.

## Requirements

- [ ] Configure and verify real Resend before production final.
- [ ] Validate a real email send first in staging using `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=resend`.
- [ ] Define production env expectations: `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, real `RESEND_API_KEY`, real `RESEND_FROM_EMAIL`, non-placeholder secrets, and HTTPS Google redirect URI.
- [ ] Prefer separate production resources: Render service `ytpipe-production`, Neon project/db `ytpipe-production`, and QStash schedule `ytpipe-production-hourly-poll`.
- [ ] Prevent duplicate real notifications between staging and production by ensuring staging QStash is disabled, or staging email remains fake, or staging has no monitored channels before production scheduler is active.
- [ ] Keep manual Alembic migrations for production.
- [ ] Keep `/health` public and keep `/status`, docs, and internal endpoints bearer-protected.
- [ ] Do not add UI, multi-user support, new queues, Celery/Redis, or unapproved features.

## Technical Approach

Complete Resend production readiness before final production operation: create/verify the Resend account setup, verify the sender/domain, configure a real `RESEND_API_KEY` and `RESEND_FROM_EMAIL`, and run a controlled real-send smoke test in staging with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=resend`. After the smoke test, either return staging to fake mode or disable staging scheduling/monitoring to avoid duplicate real notifications.

Use separated production infrastructure where practical: Render service `ytpipe-production`, Neon project/db `ytpipe-production`, and QStash schedule `ytpipe-production-hourly-poll`. Production QStash should use the same confirmed scheduler pattern: base URL `https://qstash-us-east-1.upstash.io` when using the same US-region token model, destination `https://<production-render-service-host>/internal/run-poll`, cron `0 * * * *` UTC unless explicitly changed, QStash API auth `Authorization: Bearer <QSTASH_TOKEN>`, and forwarded app auth `Upstash-Forward-Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>`.

Configure Google OAuth for production with HTTPS redirect URI `https://<production-render-service-host>/auth/callback` and add it in Google Cloud Console. Configure production env with non-placeholder secrets and real Resend settings. Keep Alembic migrations manual: run `python -m alembic upgrade head` with production `DATABASE_URL` set.

Production smoke checklist:

1. Production Render service boots.
2. `/health` works publicly.
3. `/status` rejects missing/wrong bearer and works with the production bearer token.
4. Protected docs/OpenAPI endpoints reject missing/wrong bearer and work with the production bearer token.
5. Production DB migrations are applied manually.
6. Google OAuth callback works with the production Render redirect URI.
7. Subscription sync protected endpoint works.
8. Channel listing/toggle protected endpoints work.
9. A controlled manual `POST /internal/run-poll` works.
10. A real email is received through Resend for a controlled new-video/delivery scenario.
11. Production QStash schedule `ytpipe-production-hourly-poll` is created only after manual validation.
12. QStash logs and Render logs show successful delivery for `POST /internal/run-poll`.
13. `/status` confirms readiness, polling success, email success, quota usage, and expected monitored channel count.

Rollback/safety: if production behavior is wrong, disable production QStash first or set production monitored channels to none. If Resend delivery is wrong, stop the scheduler and correct Resend configuration before re-enabling unattended polling. Avoid simultaneous staging and production real notifications by keeping staging QStash disabled, staging email fake, or staging monitored channel count at zero while production scheduling is active.

## Implementation Steps

1. Verify Resend sender/domain and configure real Resend credentials.
2. Temporarily validate real Resend delivery in staging with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=resend`.
3. Return staging to a non-duplicating safe state: QStash disabled, email fake, or no monitored channels.
4. Create or configure separate production Render and Neon resources.
5. Configure production Google OAuth redirect URI and environment variables.
6. Run manual Alembic migrations against production Neon.
7. Execute the production smoke checklist manually.
8. Create production QStash schedule only after manual endpoint and email validation.
9. Confirm production `/status` after scheduled polling.

## Acceptance Criteria

- [ ] Real Resend sender/domain setup is verified before production final.
- [ ] A real Resend send is validated in staging with `APP_ENV=staging` and `EMAIL_DELIVERY_MODE=resend` before production scheduling is enabled.
- [ ] Production env uses `APP_ENV=production`, `EMAIL_DELIVERY_MODE=resend`, real `RESEND_API_KEY`, real `RESEND_FROM_EMAIL`, non-placeholder secrets, and HTTPS Google redirect URI.
- [ ] Production uses or explicitly evaluates separate resources: Render service `ytpipe-production`, Neon project/db `ytpipe-production`, and QStash schedule `ytpipe-production-hourly-poll`.
- [ ] Staging cannot send duplicate real notifications while production scheduler is active because staging QStash is disabled, staging email is fake, or staging has no monitored channels.
- [ ] Production Alembic migrations are run manually, not from the Render start command.
- [ ] `/health` remains public and `/status`, docs, and internal endpoints remain bearer-protected.
- [ ] Production smoke checklist passes, including OAuth, subscription sync, channel monitoring, controlled poll, real email receipt, QStash schedule/logs, quota, and `/status` readiness.
- [ ] Rollback/safety action is documented: disable production QStash or remove monitored channels if production behavior is wrong.
- [ ] No UI, multi-user support, new queues, Celery/Redis, or unapproved features are added in this follow-up.
