# Homelab Linux Migration OAuth Bootstrap Specification

## Context

YTPipe currently assumes the approved hosted MVP stack documented in `specs/architecture_snapshot.spec.md` and deployment specs: Render app hosting, Neon PostgreSQL, Upstash QStash schedules, Google OAuth, and Resend. This spec defines the recommended operational path for moving the project to a personal Linux homelab for testing/personal use.

The current homelab constraint is decisive: no HTTPS is available yet. Because runtime validation in `app/core/settings.py` requires an absolute HTTPS `GOOGLE_REDIRECT_URI` and non-local SSL DB rules outside `APP_ENV=local`, homelab Google OAuth callback must not be the primary bootstrap flow in the current state. The recommended path is to complete Google OAuth on the main PC/local repo first, persist tokens in the database there, export the database state, and restore that state onto the homelab.

This is an operational migration spec, not a production-hardening replacement. It keeps the current product behavior, favors maintainable operational artifacts over immediate source changes, and treats direct insecure exposure as temporary.

## Requirements

- [ ] Define a durable, step-by-step migration path from the current local/main-PC setup to a Linux homelab.
- [ ] Use GitHub git push/pull as the repository transfer method.
- [ ] Run the API directly on the Linux host.
- [ ] Run PostgreSQL in Docker with a named volume.
- [ ] Treat homelab Google OAuth callback as non-primary until HTTPS exists.
- [ ] Use main-PC OAuth bootstrap + DB migration as the recommended token bootstrap path.
- [ ] Preserve Google OAuth client alignment so migrated tokens remain valid on the homelab.
- [ ] Include mobile push in first-transfer scope with realistic security caveats.
- [ ] Keep the exposed API port configurable and explicitly TBD.
- [ ] Explain intermediary access/security options and how they affect staging of the migration.
- [ ] Explain why `APP_ENV=local` is the recommended current homelab mode and what trade-offs it creates.
- [ ] Keep scheduler guidance aligned with current behavior: local cron calling `POST /internal/run-poll` conservatively, with no aggressive retries and no assumption of built-in locking.
- [ ] Prefer decomposed operational artifacts for future execution: env file, systemd unit, cron entry, optional Postgres compose artifact, backup/restore docs/scripts.

## Recommended Decisions

- [x] Recommended runtime mode on homelab: `APP_ENV=local`.
- [x] Recommended OAuth bootstrap: authenticate on the main PC first, then migrate DB state to homelab.
- [x] Recommended repo transfer: GitHub remote push/pull, not ad hoc file copies.
- [x] Recommended DB shape: PostgreSQL in Docker with a named volume on the homelab host.
- [x] Recommended API process manager: `systemd` service on Linux host.
- [x] Recommended scheduler: host cron calling `POST /internal/run-poll` with bearer auth on a conservative cadence.
- [x] Recommended initial exposure posture: homelab/testing/personal use only; avoid internet-direct exposure unless temporary and explicitly accepted.
- [x] Recommended future hardening path: use Tailscale as the chosen private-network intermediary for the current homelab/testing phase; only add public HTTPS later if it becomes necessary.
- [x] Recommended operational strategy: produce separate operational artifacts instead of forcing a single monolithic deployment setup.

## Non-Goals / Constraints

- [ ] This spec does not convert the homelab deployment into a production-grade, internet-safe architecture.
- [ ] This spec does not require immediate source-code changes as the first migration step.
- [ ] This spec does not assume a Dockerized API runtime; API-on-host is the chosen path.
- [ ] This spec does not add a distributed scheduler, worker queue, Redis, Celery, or poll locking.
- [ ] This spec does not replace current Google OAuth credentials unless explicitly necessary.
- [ ] This spec does not define the final exposed port; the port remains TBD and must stay configurable.
- [ ] This spec does not make non-HTTPS mobile API access safe; any such access remains temporary/testing-only.

## Technical Approach

Treat the migration as an operational bootstrap and state-transfer exercise:

1. Keep the codebase authoritative in GitHub.
2. Bootstrap OAuth and any user/subscription state from the main PC where the local auth flow is already valid.
3. Export database state only after tokens are known-good.
4. Prepare Linux runtime independently from application state restore.
5. Stand up homelab Postgres in Docker with a named volume.
6. Restore the exported database into homelab Postgres.
7. Configure the homelab app with env values that preserve token/client compatibility.
8. Run the API directly on the host under `systemd`.
9. Validate endpoints manually before enabling cron.
10. Add intermediary access hardening later without redoing the initial bootstrap path unless OAuth client/redirect rules change.

Future execution should prefer small operational artifacts such as:

- Postgres Docker/Compose artifact only for the database.
- `.env` or env-file contract documentation.
- `systemd` unit for the API.
- cron entry for polling.
- DB backup/restore instructions or scripts.
- optional reverse-proxy or private-network layer as a separate phase, with Tailscale treated as its own operational artifact/layer rather than a prerequisite for initial OAuth bootstrap.

## Deployment Topology

Recommended first-transfer topology:

- GitHub: canonical code transfer path.
- Linux host process: FastAPI/Uvicorn app running directly on the homelab host.
- Docker on Linux host: PostgreSQL container with named volume for persistence.
- Host cron: triggers `POST /internal/run-poll` against the local API.
- Optional external access: Tailscale is the selected later intermediary layer for personal/testing remote access; temporary direct exposure remains opt-in and explicitly accepted only.

Baseline trust boundaries:

- `/health` remains public if exposed.
- `/status` and `/internal/*` remain bearer-protected.
- Mobile endpoints can function from the backend side, but client-to-API bearer usage over plain HTTP is insecure.

## Bootstrap OAuth Plan

Recommended bootstrap flow:

1. On the main PC/local repo, use the existing local-compatible OAuth flow to authenticate with Google.
2. Confirm that OAuth tokens, user record, and any synced subscription state are persisted in the local database.
3. If desired, run one manual subscription sync before export so the homelab starts from a fuller channel catalog.
4. Export the local database only after OAuth is confirmed healthy.
5. Restore that database into homelab Postgres.
6. Configure homelab env vars so `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` match the client used during local bootstrap.
7. Do not treat homelab `/auth/callback` as required for first transfer.

Durability caveat:

- If the Google refresh token is revoked, expires in a way that requires re-consent, or the OAuth client changes, repeat the bootstrap on the main PC and migrate the refreshed DB state again.

## Migration Stages / Implementation Steps

1. **Local repo sanity + OAuth bootstrap on main PC**
   - Confirm the local repo is current and pushed to GitHub.
   - Confirm local env values are the intended source of truth for OAuth bootstrap.
   - Perform Google OAuth locally and verify the user/token state is stored in the DB.

2. **Local DB export after OAuth + optional subscription sync**
   - Optionally run subscription sync so the migrated DB includes a current channel catalog.
   - Export the database after OAuth success and any desired sync activity.
   - Store the export securely because it contains live auth state.

3. **Homelab repo pull + Linux runtime prep**
   - Install required host runtime dependencies for the API.
   - Pull the repo from GitHub onto the Linux host.
   - Prepare a dedicated app directory, runtime user, logs path, and env-file location.

4. **Postgres Docker setup with named volume**
   - Create a PostgreSQL container on the homelab host.
   - Use a named Docker volume for persistent DB storage.
   - Keep container lifecycle and DB credentials documented in a small operational artifact.

5. **DB restore**
   - Create the target database/schema as needed.
   - Restore the exported main-PC DB into the homelab Postgres instance.
   - Verify that token-bearing tables and core application tables are present after restore.

6. **Homelab `.env` creation preserving key secrets/Google client alignment**
   - Set `APP_ENV=local`.
   - Preserve or intentionally rotate `APP_SECRET_KEY`, `INTERNAL_API_BEARER_TOKEN`, and `MOBILE_API_BEARER_TOKEN`.
   - Point `DATABASE_URL` at homelab Postgres.
   - Keep `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` aligned with the migrated token source.
   - Set `GOOGLE_REDIRECT_URI` only as a local-compatible placeholder/contract value for current homelab mode; do not rely on it for first-transfer auth.
   - Carry forward push/email/quota settings intentionally rather than implicitly.

7. **API service setup (`systemd` recommended)**
   - Create a dedicated `systemd` unit for the API.
   - Ensure the service reads the env file, starts on boot, and restarts conservatively.
   - Keep the API host/port configurable; exposed port remains TBD.

8. **Manual smoke validation**
   - Verify startup and DB connectivity.
   - Verify public `GET /health`.
   - Verify bearer-protected `GET /status`.
   - Verify bearer-protected `POST /internal/run-poll` manually.
   - Review logs/status after the manual poll for expected sequential processing behavior.

9. **cron scheduler setup**
   - Add a host cron entry that calls `POST /internal/run-poll` with bearer auth.
   - Use a conservative cadence.
   - Do not add aggressive retries because there is no built-in scheduler lock/overlap guard.
   - Prefer one simple authoritative cron entry over multiple competing schedulers.

10. **Optional intermediary exposure hardening path later**
    - Add Tailscale first if personal/testing remote access is needed after initial transfer; treat it as a separate operational layer, not a prerequisite for the OAuth bootstrap itself.
    - Add public HTTPS only if a durable public URL becomes necessary.
    - Revisit whether homelab OAuth callback becomes viable after HTTPS is introduced.
    - Reassess whether remote/mobile access should remain enabled once the chosen intermediary is in place.

## Environment Contract

Recommended homelab env contract for first transfer:

| Variable | Recommendation / implication |
| --- | --- |
| `APP_ENV` | `local` on homelab for the current no-HTTPS state. |
| `APP_HOST` | Bind intentionally for the chosen access pattern. |
| `APP_PORT` | Configurable; final exposed port is TBD. |
| `APP_SECRET_KEY` | Required; preserve or rotate deliberately. |
| `INTERNAL_API_BEARER_TOKEN` | Required for `/status` and `/internal/*` operational calls. |
| `MOBILE_API_BEARER_TOKEN` | Required if mobile push/mobile endpoints remain in scope. |
| `DATABASE_URL` | Homelab Postgres SQLAlchemy URL; local mode allows local DB/no SSL. |
| `GOOGLE_CLIENT_ID` | Must match the OAuth client used to mint migrated tokens. |
| `GOOGLE_CLIENT_SECRET` | Must match the OAuth client used to mint migrated tokens. |
| `GOOGLE_REDIRECT_URI` | Not the primary homelab auth path in this stage; keep compatible with current local-mode operation. |
| `POLL_QUOTA_DAILY_BUDGET` | Carry forward intentionally. |
| `POLL_QUOTA_SAFETY_STOP_ENABLED` | Carry forward intentionally. |
| `EMAIL_DELIVERY_MODE` | Keep intentionally set based on actual homelab notification expectations. |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | Preserve if email delivery remains active. |
| `PUSH_NOTIFICATIONS_ENABLED` | Include intentionally; do not assume secure mobile API exposure. |
| `EXPO_*` settings | Preserve if push remains enabled. |

Secret-handling rules:

- Do not commit secrets.
- Treat DB exports and env files as sensitive artifacts.
- Keep the homelab Google client values aligned with the bootstrap environment until a deliberate re-bootstrap occurs.

## Intermediary Access Options

### Option A: Direct temporary internet exposure

Use a router/NAT rule to expose the API port directly over HTTP.

- Pros: fastest path, least setup, viable for short-lived manual testing.
- Cons: no HTTPS, bearer token exposure risk, poor fit for mobile access, not suitable as a durable remote-access posture.
- Stage effect: does not block initial migration, but should be treated as temporary only.

### Option B: Reverse proxy with HTTPS

Place Caddy or Nginx + Let's Encrypt in front of the host API.

- Pros: HTTPS for browser/mobile/API access, cleaner external URL, future path to valid homelab OAuth callback.
- Cons: requires domain/DNS/certificate setup and additional operational surface.
- Stage effect: possible future step if a public HTTPS URL becomes necessary and especially if homelab OAuth callback should eventually work directly.

### Option C: Private-network intermediary

Use a VPN or brokered private-access layer such as Tailscale, WireGuard, or a Cloudflare Tunnel-style private access pattern.

- Pros: safer remote access than raw direct exposure, can avoid broad public exposure, often sufficient for personal admin/mobile testing.
- Cons: still requires client/device enrollment or broker setup; may not solve Google OAuth callback needs unless paired with valid HTTPS/public callback strategy.
- Stage effect: strong recommended near-term access hardening if the goal is personal remote access before full public HTTPS setup.

Selected current-project path in this category:

- Tailscale is the selected recommended intermediary for this project's current homelab/testing phase.
- Cloudflare Tunnel remains a possible future option only if a public HTTPS URL becomes necessary.
- A VPS reverse proxy is not the recommended current path for this homelab phase.

### Recommended intermediary decision

- Recommended current migration path: complete the first homelab transfer without depending on an intermediary.
- Recommended safer next step: add Tailscale as the private-network intermediary for personal/testing access.
- Recommended public-access path later: add a reverse proxy with HTTPS only if durable public remote/mobile use or homelab OAuth callback is desired.
- Cloudflare Tunnel remains a future option only when a public HTTPS URL is actually needed.
- VPS reverse proxy is not the recommended current path.
- Not recommended except temporarily: direct internet HTTP exposure.

## APP_ENV=local Implications

Why `APP_ENV=local` is recommended now:

- Current runtime validation returns early for `local` and avoids the non-local requirements that would otherwise fail the homelab setup today.
- Non-local modes currently require:
  - absolute HTTPS `GOOGLE_REDIRECT_URI`
  - non-local/non-loopback `DATABASE_URL`
  - `sslmode=require` on the DB connection
- The homelab currently has no HTTPS and intentionally uses local host-to-container DB networking, so `local` is the compatible operating mode.

What `APP_ENV=local` implies:

- The homelab is operating in a local/developer-style runtime posture even if it is reachable from other devices.
- Config validation is less strict than staging/production.
- Local DB connection and non-HTTPS redirect assumptions are tolerated.
- Homelab OAuth callback is not being treated as the first-class authentication flow.

Trade-offs / limitations:

- This mode is operationally convenient but weaker as a safety rail.
- It can mask configuration problems that would surface in stricter envs.
- It is a poor fit for durable public exposure.
- It does not make bearer-auth-over-HTTP safe.
- It should be understood as a compatibility bridge for homelab migration, not an end-state deployment posture.

## Mobile Push Considerations

Mobile push is in scope for first transfer with realistic caveats:

- Backend-to-Expo outbound calls can work from the homelab if network egress is available and push settings/tokens are preserved.
- Existing push state and installation records can migrate with the database if those tables are included in export/restore.
- `MOBILE_API_BEARER_TOKEN` must remain protected and must not be exposed casually.
- Mobile app calls to a non-HTTPS homelab API are insecure because bearer tokens can be intercepted.
- Therefore, mobile API usage against direct HTTP homelab access must be treated as temporary/testing-only.
- Safer durable mobile use should wait for HTTPS or a private intermediary that materially reduces exposure risk.

Recommended practical stance:

- Allow backend push capability in the first transfer.
- Allow manual/testing mobile access only if the user explicitly accepts the HTTP bearer risk.
- Do not present first-transfer mobile access as production-safe remote operation.

## Validation / Verification

Minimum validation checklist after migration:

1. Repo on homelab matches the intended GitHub revision.
2. Postgres container is running and using the named volume.
3. Restored database contains user/token/application state.
4. API starts successfully under the chosen Linux service model.
5. `GET /health` returns success.
6. `GET /status` rejects missing bearer and succeeds with the correct bearer.
7. `POST /internal/run-poll` rejects missing bearer and succeeds with the correct bearer.
8. Manual poll does not assume concurrent-safe scheduling and completes without aggressive retry loops.
9. If email is enabled, delivery path still works from homelab.
10. If push is enabled, outbound Expo communication still works from homelab.
11. cron executes at the intended cadence and does not create overlapping noisy retries.
12. Operational logs are sufficient to diagnose startup, DB restore issues, and poll failures.

Optional higher-confidence checks:

- run one manual subscription sync if the operator wants to verify post-migration Google API access using the restored tokens
- verify that a known monitored channel poll still updates status/activity as expected
- verify that mobile endpoints remain bearer-protected before any remote/mobile testing

## Rollback

Recommended rollback path:

1. Stop the homelab API service.
2. Disable the cron entry so polling stops cleanly.
3. Preserve logs and the restored DB state for diagnosis.
4. If needed, discard and recreate the homelab Postgres container/restore from a known-good export.
5. If token/client mismatch is suspected, return to the main PC, repeat OAuth bootstrap, export a fresh DB snapshot, and restore again.
6. If homelab access posture is the issue, keep the service LAN-only until a safer intermediary is added.

## Acceptance Criteria

- [ ] The spec states that this homelab migration is for testing/personal use and is not a production-hardening substitute.
- [ ] The recommended path explicitly uses local/main-PC OAuth bootstrap followed by DB migration.
- [ ] The spec states that homelab Google OAuth callback is not the primary flow while HTTPS is unavailable.
- [ ] The spec recommends `APP_ENV=local` and explains both why it is required now and what trade-offs it creates.
- [ ] The API-on-host plus Postgres-in-Docker-with-named-volume topology is explicitly documented.
- [ ] GitHub push/pull is the defined repo transfer method.
- [ ] The staged sequence covers local bootstrap, DB export, repo pull, Linux prep, Postgres setup, DB restore, env creation, API service, smoke test, cron, and later hardening.
- [ ] The env contract explicitly requires preserving `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` alignment with migrated token state.
- [ ] The spec warns that refresh-token revocation or OAuth client changes require repeating main-PC bootstrap and DB migration.
- [ ] The spec documents `/health` as public and `/status` plus internal endpoints as bearer-protected.
- [ ] The spec recommends host cron for `POST /internal/run-poll` with conservative cadence and no aggressive retries because locking/overlap protection is not built in.
- [ ] The spec includes intermediary comparison for temporary direct exposure, HTTPS reverse proxy, and private-network access, with an explicit recommendation.
- [ ] The spec includes mobile push in scope with explicit caveats about non-HTTPS bearer security.
- [ ] The spec recommends decomposed future operational artifacts rather than a monolithic setup.
