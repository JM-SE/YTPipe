# Notifications Specification

## Context
This phase adds the MVP email delivery path on top of polling detection. Authoritative references: `specs/retry_and_deduplication_rules.spec.md`, `specs/polling_flow_definition.spec.md`, and `specs/architecture_snapshot.spec.md`.

## Requirements
- [ ] Integrate Resend as the only MVP email provider.
- [ ] Create `NotificationDelivery` records for detected new videos.
- [ ] Implement the send path for initial delivery attempts during poll processing.
- [ ] Support `pending_retry`, `delivered`, and `failed` delivery states.
- [ ] Retry `pending_retry` deliveries on the next polling cycle only.
- [ ] In scope: provider integration, delivery state transitions, attempt recording, retry pickup on next cycle.
- [ ] Out of scope: alternative email providers, more than one retry, background queue infrastructure.

## Technical Approach
Use Resend only, per `specs/architecture_snapshot.spec.md`. Apply the retry policy from `specs/retry_and_deduplication_rules.spec.md`: initial send during poll processing, mark retryable failures `pending_retry`, retry once on the next cycle, mark permanent failures `failed`, and mark successful sends `delivered`. Keep retry pickup inside the same polling-cycle contract described in `specs/polling_flow_definition.spec.md`.

## Implementation Steps
1. Add Resend client configuration and an email send service aligned with the approved provider contract.
2. Create or confirm `NotificationDelivery` during new-video processing before attempting the send.
3. Classify send outcomes into delivered, retryable failure, or permanent failure and persist status fields.
4. During each polling cycle, load existing `pending_retry` deliveries eligible for one retry and attempt them.
5. Update delivery attempt counts, timestamps, and final status after each send attempt.
6. End the phase with a local testing handoff that covers step-by-step notification testing, a short manual checklist, and a precise note on any external prerequisite needed for full delivery verification.

## Acceptance Criteria
- [ ] New video processing creates or reuses the correct `NotificationDelivery` record before send.
- [ ] A successful send marks the delivery `delivered`.
- [ ] A retryable initial failure marks the delivery `pending_retry`.
- [ ] The next polling cycle attempts exactly one retry for `pending_retry` deliveries.
- [ ] A permanent failure, or a failed retry, leaves the delivery in `failed` with no further automatic attempts.
- [ ] Phase completion includes a required local testing handoff with step-by-step instructions, a short manual checklist, and any explicit limit on what cannot be fully tested locally.
