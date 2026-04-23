# Deployment Readiness Specification

## Context
This phase prepares the MVP for the approved free-tier deployment model. Authoritative references: `specs/architecture_snapshot.spec.md` and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Define the production environment variable contract for the approved stack and integrations.
- [ ] Capture deployment assumptions for Render, Neon, and cron-job.org.
- [ ] Add production-safe configuration checks before unattended operation.
- [ ] Provide a smoke-test checklist for first deploy and post-change verification.
- [ ] Document free-tier operational concerns relevant to the approved MVP.
- [ ] In scope: env contract, deployment assumptions, startup/config validation expectations, smoke-test checklist, operational cautions.
- [ ] Out of scope: alternative hosting targets, infrastructure expansion, new runtime features.

## Technical Approach
Keep deployment aligned with the frozen MVP hosting model: Render app, Neon PostgreSQL, and cron-job.org triggering `POST /internal/run-poll`. Define the environment contract for app settings, DB connection, Google OAuth credentials, bearer secret, YouTube quota controls, and Resend settings. Require startup-time validation for missing critical config so production misconfiguration fails clearly rather than silently.

## Implementation Steps
1. Define the required and optional environment variables for app runtime, database, OAuth, polling security, quota controls, and email delivery.
2. Document the expected Render service behavior, Neon connection assumptions, and cron-job.org trigger contract.
3. Specify production-safe config checks for startup, including missing secrets, invalid URLs, and unsafe placeholder values.
4. Write a concise smoke-test checklist covering startup, DB migration, auth, import, poll, status, and email delivery.
5. Capture free-tier concerns such as cold starts, cron overlap avoidance, quota budget tuning, and provider reliability checks.
6. End the phase with a local testing handoff that explains which deployment-readiness checks can be exercised locally, includes a short manual checklist, and names any missing deployment prerequisite for the rest.

## Acceptance Criteria
- [ ] The spec names the required environment variables and their operational purpose.
- [ ] The deployment assumptions explicitly match Render, Neon, and cron-job.org.
- [ ] Production startup is required to fail clearly when critical configuration is missing or unsafe.
- [ ] A smoke-test checklist exists for verifying the deployed MVP end to end.
- [ ] Free-tier operational concerns are called out without expanding the product scope.
- [ ] Phase completion includes a required local testing handoff that separates local checks from deployment-only checks and states the exact missing prerequisite for anything not locally testable.
