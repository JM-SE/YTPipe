# Channel Catalog API Specification

## Context

This post-MVP phase makes the channel catalog API UI-friendly for a future React Native/Expo companion app. Mobile notifications and detection remain limited to explicitly monitored channels. Non-monitored channels are catalog entries that can be searched, listed, and activated, but are not polled, detected, or notified until monitoring is enabled.

## Requirements

- [ ] Enhance the channel list endpoint with `monitoring=monitored|unmonitored|all`, default `monitored`.
- [ ] Support `query=<text>` filtering by channel title/name, case-insensitive where practical for the DB.
- [ ] Support `limit` and `offset` pagination with sensible bounds.
- [ ] Response includes `channels` and `pagination` with `limit`, `offset`, and `total`.
- [ ] Channel item includes internal `channel_id`, `youtube_channel_id`, `title`, `is_monitored`, baseline fields, and latest detected video summary when available from stored data.
- [ ] Existing `PATCH /internal/channels/{channel_id}/monitoring` remains the toggle endpoint and documents body/response examples.
- [ ] Default list shows only monitored channels so the app opens to the active set.
- [ ] Non-monitored channels can be searched/listed and activated, but are not polled/detected/notified until monitored.
- [ ] Protect catalog endpoints with the mobile/admin auth contract from Phase 08.
- [ ] In scope: catalog list filtering, pagination, activation docs, Swagger examples, tests.
- [ ] Out of scope: polling non-monitored channels, YouTube live search, UI/RN implementation, push notifications.

## Technical Approach

Keep the catalog backed by stored channel and user-channel records. Filtering should operate on local persisted data only. Default to monitored channels for the active mobile home view. Use explicit response models for channel items and pagination. Include latest detected video summary only from stored `Video`/state data; do not call YouTube from the list endpoint.

The monitoring toggle remains the existing patch endpoint. It should preserve baseline semantics: enabling monitoring makes the channel eligible for polling, and baseline/detection behavior continues to be owned by the polling flow.

## Implementation Steps

1. Define request query parameters and response models for channel catalog listing.
2. Implement monitoring filter defaulting to `monitored`.
3. Implement local title/name query filtering and bounded `limit`/`offset` pagination.
4. Include baseline fields and latest stored detected video summary when available.
5. Update monitoring toggle docs with request/response examples and error examples.
6. Add tests for defaults, filters, pagination, activation, and non-monitored exclusion from polling assumptions.
7. Add Swagger examples for monitored list, unmonitored search, all channels, pagination, and monitoring toggle.
8. End the phase with a local/manual Swagger handoff for catalog browsing and activation.

## Acceptance Criteria

- [ ] Default channel list returns monitored channels only.
- [ ] `monitoring=monitored|unmonitored|all` works as documented.
- [ ] `query` filters by channel title/name using a practical case-insensitive approach.
- [ ] `limit` and `offset` are bounded and reflected in `pagination` with `total`.
- [ ] Channel items include required identifiers, title, monitoring state, baseline fields, and latest stored video summary when available.
- [ ] Monitoring toggle endpoint remains compatible and has Swagger body/response examples.
- [ ] Non-monitored channels can be found and activated but are not polled/detected/notified until monitored.
- [ ] Tests cover defaults, filters, pagination, activation, and Swagger/OpenAPI examples where practical.
- [ ] Phase completion includes a local/manual Swagger handoff with sample catalog and toggle calls.
