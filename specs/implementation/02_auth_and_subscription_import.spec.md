# Auth And Subscription Import Specification

## Context
This phase adds the single-user Google auth and initial subscription import flow. Authoritative references: `specs/architecture_snapshot.spec.md`, `specs/data_model_draft.spec.md`, and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Implement Google OAuth for the single supported account using minimum required auth and read scopes only.
- [ ] Persist `OAuthAccount` data, including refresh-token-capable credentials.
- [ ] Define the token refresh handling contract so expired access tokens are refreshed automatically when possible.
- [ ] Import subscriptions and create or update canonical `Channel` and `UserChannel` records.
- [ ] Imported channels must not be monitored by default.
- [ ] OAuth callback must not synchronously perform expensive import-plus-baseline work.
- [ ] In scope: auth entry/callback flow, OAuth persistence, subscription catalog sync, non-monitored import behavior, `SyncState` updates for subscription sync.
- [ ] Out of scope: polling for new uploads after import, email sending, historical backfill, multi-user behavior.

## Technical Approach
Follow the minimum-scope and manual re-auth boundaries from `specs/architecture_snapshot.spec.md` and `specs/youtube_notifier_specs_v_2.spec.md`. Persist a single Google OAuth account per user via `OAuthAccount`. The OAuth callback completes auth and token persistence, then may trigger only lightweight sync metadata behavior. Subscription sync upserts `Channel` records and creates or updates `UserChannel` rows as an imported catalog with `is_monitored = false` by default and no per-channel baseline establishment.

## Implementation Steps
1. Add Google OAuth start and callback endpoints for the single-user flow.
2. Persist or update the approved `OAuthAccount` record for provider `google`.
3. Define and implement access-token refresh handling before YouTube API calls that require valid credentials.
4. Ensure the OAuth callback returns after auth persistence and does not synchronously execute full catalog import plus per-channel baseline work.
5. Fetch subscriptions from YouTube and upsert `Channel` plus `UserChannel` records with monitoring disabled by default.
6. Record subscription-catalog sync success or error in `SyncState` without creating baseline or notification state for all channels.
7. End the phase with a local testing handoff that explains local auth and import verification steps, provides a short manual checklist, and explicitly notes any non-local prerequisites such as Google OAuth credentials.

## Acceptance Criteria
- [ ] OAuth uses only the minimum required authentication and read scopes.
- [ ] The system persists one `OAuthAccount` per user-provider pair and stores refresh-capable credentials.
- [ ] If an access token is expired but refreshable, the contract refreshes it without forcing a new login.
- [ ] Subscription import creates or updates `Channel` and `UserChannel` records for subscribed channels.
- [ ] Imported `UserChannel` rows default to `is_monitored = false` unless explicitly changed by the channel-management flow.
- [ ] The OAuth callback does not synchronously perform the full expensive subscription import plus baseline workflow.
- [ ] Subscription sync does not create `Video` or `NotificationDelivery` records and does not establish baseline for all channels.
- [ ] Phase completion includes a local testing handoff with step-by-step instructions, a short manual checklist, and any required note about what cannot be fully tested locally and why.
