# Mobile API Auth And Swagger Specification

## Context

This post-MVP phase prepares the backend auth and documentation contract for a future personal React Native/Expo companion app. The app is a single-user/admin companion surface, not public multi-user authentication. Existing QStash/internal automation continues to use `INTERNAL_API_BEARER_TOKEN`. Existing docs behavior remains: local docs are public; staging/production docs are bearer-protected.

## Requirements

- [ ] Add `MOBILE_API_BEARER_TOKEN` as a separate rotatable token for future mobile/admin app access.
- [ ] Keep `INTERNAL_API_BEARER_TOKEN` for QStash and internal automation.
- [ ] Mobile/manual admin endpoints accept the mobile token; internal automation endpoints continue accepting the internal token where appropriate.
- [ ] Avoid hardcoding secrets; the future mobile app stores the mobile token in secure on-device storage.
- [ ] Swagger/OpenAPI remains usable: local public, staging/production bearer-protected.
- [ ] New or updated endpoints include tags, summaries, descriptions, response models, query/body docs, examples where useful, and error examples where practical.
- [ ] `/health` remains public.
- [ ] In scope: bearer-token contract, protected docs contract, API documentation quality conventions.
- [ ] Out of scope: full OAuth login for mobile users, multi-user auth, public app auth, push notifications, and React Native implementation.

## Technical Approach

Extend bearer validation so the backend can distinguish or allow token classes by endpoint purpose. QStash/internal automation keeps using `INTERNAL_API_BEARER_TOKEN`; mobile/admin endpoints use `MOBILE_API_BEARER_TOKEN`. Endpoints that are both manual-admin and operational may explicitly allow both tokens only when the spec for that endpoint requires it.

Keep security boundaries explicit in endpoint dependencies and OpenAPI security declarations. `/health` stays unauthenticated. `/status`, docs, and mobile/admin endpoints remain protected outside local development. Use Pydantic response models and request models to improve Swagger examples and schema quality.

## Implementation Steps

1. Add settings contract for `MOBILE_API_BEARER_TOKEN` with non-placeholder validation for deployed environments.
2. Add or update bearer dependencies to support internal-only, mobile/admin-only, and explicitly shared access patterns.
3. Preserve QStash/internal automation access through `INTERNAL_API_BEARER_TOKEN`.
4. Preserve local public docs and staging/production protected docs behavior.
5. Update endpoint docs conventions: tags, summaries, descriptions, models, examples, query/body metadata, and practical error examples.
6. Add tests for token separation, docs protection, `/health` public access, and representative Swagger/OpenAPI metadata.
7. End the phase with a local/manual Swagger testing handoff covering token usage and example requests.

## Acceptance Criteria

- [ ] `MOBILE_API_BEARER_TOKEN` exists as a separate configured token from `INTERNAL_API_BEARER_TOKEN`.
- [ ] QStash/internal automation endpoints continue to work with the internal token.
- [ ] Mobile/admin endpoints accept the mobile token according to their endpoint contract.
- [ ] Secrets are not hardcoded or exposed in examples.
- [ ] `/health` remains public.
- [ ] Local Swagger/OpenAPI remains public; staging/production docs remain bearer-protected.
- [ ] New/updated endpoints have useful tags, summaries, descriptions, response models, query/body docs, and examples.
- [ ] Tests cover token separation and docs/security behavior.
- [ ] Phase completion includes a local/manual Swagger handoff with token setup, sample calls, and expected errors.
