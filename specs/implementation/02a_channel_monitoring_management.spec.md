# Channel Monitoring Management Specification

## Context
This appendix phase adds the MVP internal management surface required because there is no UI yet. Authoritative references: `specs/architecture_snapshot.spec.md`, `specs/data_model_draft.spec.md`, and `specs/youtube_notifier_specs_v_2.spec.md`.

## Requirements
- [ ] Provide an internal endpoint to list imported channels for the single user.
- [ ] Provide an internal endpoint to set channel monitoring on or off for a specific channel.
- [ ] Use one consistent endpoint style: `GET /internal/channels` and `PATCH /internal/channels/{channel_id}/monitoring`.
- [ ] Enabling monitoring must mark the channel as eligible for polling without requiring a UI.
- [ ] Disabling monitoring must remove the channel from future polling.
- [ ] In scope: internal management endpoints, `UserChannel.is_monitored` updates, baseline-state handling rules needed for activation.
- [ ] Out of scope: public UI, bulk-edit UX, polling execution, email delivery.

## Technical Approach
Treat this as a temporary MVP management surface that replaces a UI. `GET /internal/channels` returns imported channel catalog entries and current monitoring state for the single user. `PATCH /internal/channels/{channel_id}/monitoring` accepts a boolean contract to set monitoring enabled or disabled. When monitoring is enabled, the system sets `is_monitored = true` and leaves baseline fields unset unless an implementation chooses to stamp an activation timestamp; polling remains responsible for establishing baseline if `baseline_established_at` or `last_seen_video_id` is still missing. When monitoring is disabled, the system sets `is_monitored = false` and excludes the channel from future polling.

## Implementation Steps
1. Add `GET /internal/channels` to list imported channel catalog records and their monitoring state.
2. Add `PATCH /internal/channels/{channel_id}/monitoring` with a boolean request contract to set monitoring on or off.
3. Update `UserChannel.is_monitored` through this endpoint instead of enabling monitoring during subscription import.
4. Preserve or clear baseline-adjacent fields only according to the chosen implementation rule, while keeping polling responsible for no-notify baseline establishment when baseline is missing.
5. End the phase with a local testing handoff that explains how to list channels, enable monitoring, disable monitoring, and verify the stored state change.

## Acceptance Criteria
- [ ] `GET /internal/channels` lists imported channels for the single user and includes monitoring state.
- [ ] `PATCH /internal/channels/{channel_id}/monitoring` consistently sets monitoring on or off using one boolean contract.
- [ ] Newly imported channels remain unmonitored until this management flow explicitly enables them.
- [ ] Disabling monitoring removes a channel from future polling eligibility.
- [ ] Phase completion includes a local testing handoff with step-by-step instructions and a short manual checklist.
