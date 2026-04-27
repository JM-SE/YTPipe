# Protected Developer API Docs Specification

## Context
Option C is approved for API docs: keep FastAPI developer docs available, but require the existing internal bearer token outside local development. This preserves deployment diagnostics without making developer docs public in staging or production.

## Requirements
- [ ] FastAPI Swagger UI, OpenAPI JSON, and ReDoc remain available.
- [ ] In local development, docs may remain public for convenience.
- [ ] In staging and production, docs, OpenAPI JSON, and ReDoc require `Authorization: Bearer <INTERNAL_API_BEARER_TOKEN>`.
- [ ] `/health` remains public and unaffected.
- [ ] Existing internal API endpoints remain bearer-protected as before.
- [ ] OpenAPI declares HTTP bearer authentication so Swagger UI shows an Authorize flow and protected endpoints can be exercised from docs.
- [ ] Do not change any product API contracts, endpoint paths, request bodies, or response bodies.

## Technical Approach
Protect only the developer documentation surfaces in non-local environments using the same internal bearer token already used for internal API protection. Keep `/health` excluded from bearer checks. Ensure generated OpenAPI metadata includes an HTTP bearer security scheme and applies it to protected internal endpoints so Swagger UI users can authorize once and call protected endpoints from the docs.

The implementation should avoid introducing new auth mechanisms, new environment variables, or product endpoint contract changes. Local behavior may keep FastAPI docs/OpenAPI/ReDoc public to reduce development friction.

## Implementation Steps
1. Identify the current FastAPI docs, OpenAPI JSON, and ReDoc routes.
2. Apply the existing internal bearer-token guard to those documentation routes only when `APP_ENV` is staging or production.
3. Preserve local public access to docs/OpenAPI/ReDoc.
4. Confirm `/health` remains public.
5. Confirm existing internal endpoints keep their current bearer-token behavior.
6. Add OpenAPI HTTP bearer security metadata for protected internal endpoints.

## Acceptance Criteria
- [ ] Local Swagger UI, OpenAPI JSON, and ReDoc are accessible without a bearer token.
- [ ] Staging and production Swagger UI, OpenAPI JSON, and ReDoc reject missing or wrong bearer tokens.
- [ ] Staging and production Swagger UI, OpenAPI JSON, and ReDoc are accessible with the correct internal bearer token.
- [ ] OpenAPI includes an HTTP bearer security scheme.
- [ ] Swagger UI shows an Authorize flow and can call protected internal endpoints after authorization.
- [ ] Existing internal endpoints still reject missing bearer tokens.
- [ ] `/health` remains publicly accessible without a bearer token.
- [ ] No product API contract changes are introduced.
