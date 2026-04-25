# Notifications Specification

## Context
This phase adds the MVP email delivery path on top of polling detection. Authoritative references: `specs/retry_and_deduplication_rules.spec.md`, `specs/polling_flow_definition.spec.md`, and `specs/architecture_snapshot.spec.md`.

## Requirements
- [ ] Integrate Resend as the only MVP email provider.
- [ ] Use `User.email` from OAuth as the MVP notification recipient source; do not add a separate recipient env var.
- [ ] Require `RESEND_API_KEY` and `RESEND_FROM_EMAIL` for real Resend sends.
- [ ] Support development/test-only `EMAIL_DELIVERY_MODE=resend|fake`; `fake` simulates successful sends without calling Resend, updates state as sent, and must not run in production.
- [ ] Create or reuse `NotificationDelivery` records for detected new videos.
- [ ] Implement the send path for initial delivery attempts during poll processing.
- [ ] Support `pending_retry`, `delivered`, and `failed` delivery states.
- [ ] Retry `pending_retry` deliveries on the next polling cycle only.
- [ ] In scope: provider integration, delivery state transitions, attempt recording, retry pickup on next cycle.
- [ ] Out of scope: alternative email providers, more than one retry, background queue infrastructure.

## Technical Approach
Use Resend only for real MVP delivery, per `specs/architecture_snapshot.spec.md`; Resend may be configured later because local/automated logic can use fake mode. Send to `User.email` from OAuth. Real sends require `RESEND_API_KEY` and `RESEND_FROM_EMAIL`. Minimal email content is subject `Nuevo video: <video title>` and a body containing channel title, video title, and `https://www.youtube.com/watch?v=<youtube_video_id>`.

Apply the retry policy from `specs/retry_and_deduplication_rules.spec.md`: existing Phase 03 `NotificationDelivery(status="pending", attempt_count=0)` records, new records, or pending records get an initial send attempt during poll processing. Success marks `delivered`; retryable failure marks `pending_retry`; permanent failure marks `failed`. Each polling cycle loads `pending_retry` deliveries and attempts exactly one retry; retry success marks `delivered`, and any retry failure marks `failed` with no further automatic retries. Retryable failures are timeout, network/transport, HTTP 408, 429, and 5xx. Permanent failures are non-transient 4xx including 400/401/403/404, invalid payload, invalid sender/recipient, invalid credentials, and provider permanent rejection. Keep retry pickup inside the same polling-cycle contract described in `specs/polling_flow_definition.spec.md`.

## Implementation Steps
1. Add Resend client configuration, fake delivery mode, and an email send service aligned with the approved provider contract.
2. Send notifications to `User.email` and format the minimal approved subject/body content.
3. Create or confirm `NotificationDelivery` during new-video processing before attempting the send, including Phase 03 pending records.
4. Classify send outcomes into delivered, retryable failure, or permanent failure and persist status fields.
5. During each polling cycle, load existing `pending_retry` deliveries eligible for one retry and attempt them once.
6. Update delivery attempt counts, timestamps, and final status after each send attempt.
7. End the phase with a local testing handoff that covers fake-mode validation, the real Resend prerequisites, a short manual checklist, and a precise note on any external prerequisite needed for full delivery verification.

## Acceptance Criteria
- [ ] New video processing creates or reuses the correct `NotificationDelivery` record before send.
- [ ] Phase 03 `NotificationDelivery(status="pending", attempt_count=0)` records receive an initial send attempt during poll processing.
- [ ] Notifications are addressed to `User.email` from OAuth.
- [ ] Real-send configuration requires `RESEND_API_KEY` and `RESEND_FROM_EMAIL`; Resend is the only real MVP provider.
- [ ] `EMAIL_DELIVERY_MODE=fake` simulates success without Resend, updates delivery state as sent, is usable for local/test validation, and is rejected or blocked in production.
- [ ] Email content uses subject `Nuevo video: <video title>` and includes channel title, video title, and the YouTube watch URL in the body.
- [ ] A successful send marks the delivery `delivered`.
- [ ] A retryable initial failure marks the delivery `pending_retry`.
- [ ] The next polling cycle attempts exactly one retry for `pending_retry` deliveries.
- [ ] A permanent failure, or a failed retry, leaves the delivery in `failed` with no further automatic attempts.
- [ ] Retryable and permanent provider failures follow the approved mappings in this spec.
- [ ] Phase completion includes a required local testing handoff with fake-mode validation steps, what requires real Resend, a short manual checklist, and any explicit limit on what cannot be fully tested locally.
