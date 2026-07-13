# Retry And Deduplication Rules Specification

## Context
Compact MVP rules for safe delivery retries and notification idempotency, covering email, content pipeline (transcript/summary/Telegram), and fallback notifications.

## Requirements
- [x] Define retryable failures, non-retryable failures, status transitions, and uniqueness guarantees.
- [x] Content pipeline stages (transcript, summary, Telegram) each retry up to 3 total attempts.
- [x] When a stage fails permanently after 3 attempts, a fallback Telegram message is sent with the reason.
- [x] Fallback Telegram delivery is retried on each poll until successful.

## Technical Approach

### Email Delivery (NotificationDelivery)
Retryable failures: timeout, 5xx response, temporary rate limit, and network or transport failure.

Non-retryable failures: invalid sender, invalid recipient, invalid credentials, malformed request, and permanent provider rejection.

Status transitions: initial send attempt happens during poll processing; if a retryable failure occurs, mark the delivery `pending_retry`; retry once on the next polling cycle; if that retry fails, mark the delivery `failed`; if a non-retryable failure occurs, mark the delivery `failed` immediately; successful send marks the delivery delivered.

### Content Pipeline (PipelineStage)
Each video has up to 4 pipeline stages tracked in the `pipeline_stages` table:

| Stage | Depends On | Purpose |
|-------|-----------|---------|
| `transcript` | — | Fetch video transcript via youtube-transcript-api |
| `summary` | transcript=completed | Generate AI summary via llama.cpp |
| `telegram` | summary=completed | Send Telegram notification with summary |
| `fallback_telegram` | any stage=failed | Send fallback message when a stage fails permanently |

Retryable failures for transcript and summary: any exception during fetch or generation.
Non-retryable failures for transcript: transcript not available for the video (zero snippets).
Retryable failures for Telegram: timeout, 5xx, 429, network/transport errors.
Non-retryable failures for Telegram: 4xx (except 429).

Status transitions per stage:
- Initial attempt during new video processing → success → `completed`
- Initial attempt fails → `pending_retry`, attempt_count incremented
- Subsequent retry on next poll cycle → success → `completed`
- After attempt_count >= max_attempts (3) → `failed`
- Upstream stage fails → downstream stages → `skipped`
- Fallback stage: created as `pending` when any stage fails, retried each poll until `completed` or non-retryable failure

**Dependency chain**: If `transcript` → `failed`, `summary` becomes `skipped`, `telegram` becomes `skipped`. If `summary` → `failed`, `telegram` becomes `skipped`. Fallback message includes the reason for the first failing stage.

### Startup Processing
On server restart, pending pipeline stages are processed with throttling:
- `PIPELINE_STARTUP_BATCH_SIZE` (default: 5) controls how many stages are processed per batch
- `PIPELINE_STARTUP_BATCH_DELAY_SECONDS` (default: 30) controls delay between batches
- Set batch size to 0 to disable startup processing entirely

### Idempotency Guarantees
- `Video.youtube_video_id` is globally unique (no duplicate video records).
- `NotificationDelivery` enforces `unique(user_id, video_id)` — one user cannot receive duplicate email notifications for the same video.
- `PipelineStage` enforces `unique(video_id, user_id, stage)` — one stage record per stage per video per user.

## Implementation Steps
1. Classify email provider errors into retryable or permanent buckets.
2. Persist `pending_retry` only for retryable failures.
3. Enforce uniqueness constraints before creating new video or delivery records.
4. Pipeline stages track transcript, summary, Telegram, and fallback with per-stage retry counts.
5. Startup processing throttles pending pipeline reprocessing to avoid system overload.

## Acceptance Criteria
- [x] Retry happens only for approved transient failures.
- [x] Permanent failures are never retried.
- [x] The delivery flow includes `pending_retry` and a single next-cycle retry for email.
- [x] The uniqueness guarantees for `Video`, `NotificationDelivery`, and `PipelineStage` are explicit.
- [x] Transcript fetch retries up to 3 total attempts per video.
- [x] Summary generation retries up to 3 total attempts per video.
- [x] Telegram notification retries up to 3 total attempts per video.
- [x] Fallback Telegram sent when any content stage fails permanently, with specific reason.
- [x] Fallback Telegram retries on each poll until delivered or non-retryable failure.
- [x] Startup reprocessing uses configurable batch throttling to avoid overload.
- [x] Downstream stages are skipped when upstream stages fail.
