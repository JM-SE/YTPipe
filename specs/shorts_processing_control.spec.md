# Global Shorts Processing Control Specification

## Context

YTPipe already classifies canonical videos through `Video.is_short`. This change
adds a deployment-wide environment flag that controls whether classified Shorts
continue through the content-processing and notification pipeline.

## Configuration Contract

Add the following setting with a safe default that preserves the current
behavior:

```env
SHORTS_PROCESSING_ENABLED=true
```

The setting is global for the running application. It is not configurable per
channel, user, or notification provider.

## Behavior

- Shorts must still be detected and persisted as canonical `Video` records.
- Detection may use the existing title marker or YouTube duration lookup; the
  flag does not disable classification.
- When `SHORTS_PROCESSING_ENABLED=false`, a detected Short must not:
  - create pipeline stages;
  - request a transcript;
  - generate a summary;
  - send Telegram or email notifications;
  - create or send mobile push notifications; or
  - enter an automatic retry path.
- The channel marker must advance after the Short is persisted so the same
  upload is not rediscovered on every poll.
- Existing pending pipeline work and notification deliveries for Shorts must
  be made terminally skipped while the flag is disabled. They must not be
  resumed automatically if the flag is later enabled.
- Existing pending mobile push deliveries for Shorts must also be marked
  terminally skipped.
- Shorts omitted while the flag is disabled are not backfilled when the flag
  is re-enabled. Only newly detected Shorts are eligible for processing after
  reactivation.
- Normal videos retain their current behavior in all cases.

## Scope of Application

The guard must apply consistently to:

- normal polling;
- missing-upload reconciliation;
- polling-cycle email delivery and retries;
- pending pipeline draining;
- application-startup pipeline processing; and
- mobile push fan-out.

## Persistence

No database migration is required. The existing string status fields may use a
terminal `skipped` value for work that was already queued when the flag was
disabled. Newly omitted Shorts only require the canonical `Video` record.

## Acceptance Criteria

- [ ] `Settings` reads `SHORTS_PROCESSING_ENABLED` as a boolean and defaults to
      `true`.
- [ ] `.env.example` documents the setting.
- [ ] A disabled flag still persists `Video.is_short` and advances the channel
      marker.
- [ ] A disabled Short causes no transcript service call.
- [ ] A disabled Short creates no pipeline stages, email delivery, Telegram
      send, or mobile push fan-out.
- [ ] Pending pipeline stages and notification deliveries for Shorts become
      terminally skipped while disabled.
- [ ] Pending mobile push deliveries for Shorts become terminally skipped while
      disabled.
- [ ] Re-enabling the flag does not process Shorts skipped during the disabled
      period.
- [ ] Normal videos are unaffected.
- [ ] The behavior is covered for polling, reconciliation, startup draining,
      and retry paths.
