# Data Model Draft Specification

## Context
Concise MVP data model draft for the approved YouTube notifier architecture.

## Requirements
- [ ] Cover exactly: User, OAuthAccount, Channel, UserChannel, Video, NotificationDelivery, SyncState.
- [ ] Include purpose, core fields, key relationships, and important unique constraints.
- [ ] Reflect approved baseline, retry, and deduplication rules.

## Technical Approach
User: single system owner record; core fields `id`, `email`, timestamps; unique `email`.

OAuthAccount: stores Google OAuth credentials for the user; core fields `id`, `user_id`, `provider`, `access_token`, `refresh_token`, `token_expiry`, timestamps; relationship to `User`; unique `(user_id, provider)`.

Channel: canonical YouTube channel record; core fields `id`, `youtube_channel_id`, `title`, `uploads_playlist_id`, timestamps; unique `youtube_channel_id`.

UserChannel: joins the user to imported channels and stores per-user channel monitoring state; core fields `user_id`, `channel_id`, `is_monitored`, `last_seen_video_id`, `baseline_established_at`, timestamps; relationships to `User` and `Channel`; unique `(user_id, channel_id)`.

`UserChannel.is_monitored` defaults to `false` when a subscription is imported into the catalog.

`UserChannel.last_seen_video_id` and `UserChannel.baseline_established_at` are monitoring-state fields set when monitoring is activated or on the first poll after activation if baseline is still missing.

Video: canonical detected video record; core fields `id`, `youtube_video_id`, `channel_id`, `title`, `published_at`, timestamps; relationship to `Channel`; `youtube_video_id` is globally unique.

NotificationDelivery: tracks email delivery state for a user-video pair; core fields `id`, `user_id`, `video_id`, `status`, `attempt_count`, `last_attempt_at`, `last_error`, timestamps; relationships to `User` and `Video`; unique `(user_id, video_id)`.

SyncState: stores process-level operational state; core fields `id`, `user_id`, `process_type`, `last_success_at`, `last_error_at`, `last_error_message`, `metadata`, timestamps; one record per process type for the user; initial `process_type` values: `subscription_sync`, `polling`, `quota`; unique `(user_id, process_type)`.

## Implementation Steps
1. Translate each entity directly into ORM models without renaming the approved domain objects.
2. Keep imported-channel and monitoring state in `UserChannel`, with baseline fields remaining unset until monitoring is activated.
3. Enforce the stated uniqueness constraints at the database level.

## Acceptance Criteria
- [ ] All seven approved entities are covered.
- [ ] `UserChannel.is_monitored` defaults to `false` on import and baseline fields remain monitoring-state only.
- [ ] `Video.youtube_video_id` is globally unique.
- [ ] `NotificationDelivery` enforces `unique(user_id, video_id)`.
- [ ] `SyncState` is defined as one record per process type with the three initial types.
