# Mobile Activity API Specification

## Context

This post-MVP phase adds a mobile-friendly read-only activity endpoint for recent detected videos and notification delivery history. Activity reflects the monitored-channel workflow only; non-monitored catalog entries do not produce detection or notification activity.

## Requirements

- [ ] Add read-only endpoint `GET /internal/activity`.
- [ ] List recent detected video and delivery activity for the monitored-channel workflow.
- [ ] Include mobile-useful fields: `activity_id` or delivery id, `video_id`, `youtube_video_id`, `video_title`, `youtube_url`, `channel_id`, `channel_title`, `delivery_status`, `detected_at` or equivalent stored timestamp, `published_at`, `last_attempt_at`, and `last_error` when relevant.
- [ ] Support `status=all|pending|delivered|pending_retry|failed`, default `all`.
- [ ] Support `limit` and `offset` pagination with sensible bounds.
- [ ] Response includes `items` and `pagination`.
- [ ] Protect the endpoint with the mobile/admin auth contract from Phase 08.
- [ ] Provide Swagger examples for common statuses.
- [ ] In scope: read-only activity list, filters, pagination, response models, Swagger examples, tests.
- [ ] Out of scope: push notifications, changing email retry policy, full audit log, UI/RN implementation.

## Technical Approach

Build `GET /internal/activity` from stored `NotificationDelivery`, `Video`, and `Channel` data. Do not trigger polling, retries, email sends, or YouTube API calls from this endpoint. Sort by recent activity using the best stored timestamp available, such as delivery attempt/update time or video publication/detection time. Generate `youtube_url` as `https://www.youtube.com/watch?v=<youtube_video_id>`.

Use explicit response models for activity items and pagination. Keep `last_error` visible only when relevant for failed or retrying deliveries. The endpoint should be safe for frequent mobile refreshes because it is read-only and paginated.

## Implementation Steps

1. Define the `GET /internal/activity` query parameters and response models.
2. Join stored delivery, video, and channel data into mobile-friendly activity items.
3. Implement status filtering with default `all`.
4. Implement bounded `limit`/`offset` pagination and total count.
5. Add Swagger examples for all activity, delivered-only, pending-retry, and failed activity.
6. Add tests for auth, default listing, status filters, pagination, URL generation, and error-field behavior.
7. End the phase with a local/manual Swagger handoff for browsing activity and validating filters.

## Acceptance Criteria

- [ ] `GET /internal/activity` exists and is read-only.
- [ ] Endpoint is protected with mobile/admin auth from Phase 08.
- [ ] Response includes `items` and `pagination`.
- [ ] Activity items include required video, channel, delivery, timestamp, URL, and relevant error fields.
- [ ] `status=all|pending|delivered|pending_retry|failed` works with default `all`.
- [ ] `limit` and `offset` are bounded and reflected in pagination metadata.
- [ ] Endpoint does not trigger polling, retries, email sends, or YouTube API calls.
- [ ] Swagger includes examples for common status filters.
- [ ] Tests cover auth, filters, pagination, URL generation, and representative response shape.
- [ ] Phase completion includes a local/manual Swagger handoff with sample activity queries.
