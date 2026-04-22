# Retry And Deduplication Rules Specification

## Context
Compact MVP rules for safe email delivery retries and notification idempotency.

## Requirements
- [ ] Define retryable failures, non-retryable failures, status transitions, and uniqueness guarantees.

## Technical Approach
Retryable failures: timeout, 5xx response, temporary rate limit, and network or transport failure.

Non-retryable failures: invalid sender, invalid recipient, invalid credentials, malformed request, and permanent provider rejection.

Status transitions: initial send attempt happens during poll processing; if a retryable failure occurs, mark the delivery `pending_retry`; retry once on the next polling cycle; if that retry fails, mark the delivery `failed`; if a non-retryable failure occurs, mark the delivery `failed` immediately; successful send marks the delivery delivered.

Idempotency guarantees: `Video.youtube_video_id` is globally unique, and `NotificationDelivery` enforces `unique(user_id, video_id)` so one user cannot receive duplicate notifications for the same video.

## Implementation Steps
1. Classify email provider errors into retryable or permanent buckets.
2. Persist `pending_retry` only for retryable failures.
3. Enforce uniqueness constraints before creating new video or delivery records.

## Acceptance Criteria
- [ ] Retry happens only for approved transient failures.
- [ ] Permanent failures are never retried.
- [ ] The delivery flow includes `pending_retry` and a single next-cycle retry.
- [ ] The uniqueness guarantees for `Video` and `NotificationDelivery` are explicit.
