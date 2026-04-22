# Agent Guardrails Specification

## Context
Implementation-boundaries contract for the downstream coding agent.

## Requirements
- [ ] State what the implementation agent may decide.
- [ ] State what the implementation agent may not decide.
- [ ] Keep all boundaries aligned with the approved MVP architecture.

## Technical Approach
The agent may decide internal folder organization, helper decomposition, non-domain utility naming, and routine implementation details that stay consistent with the approved architecture.

The agent may not replace the approved stack, change the uploads-playlist detection strategy, add UI, add multi-user support, add extra notification channels, introduce Celery or Redis, or alter the approved persistence model without approval.

The agent may not expand scope through unapproved scheduling, messaging, caching, or product-surface additions. Domain entities, polling semantics, retry rules, and status visibility are frozen by the approved specs.

## Implementation Steps
1. Use the approved specs as the source of truth for product and architecture decisions.
2. Make only local implementation choices that do not change domain behavior.
3. Escalate any requested architecture or scope deviation for approval before coding.

## Acceptance Criteria
- [ ] Allowed implementation decisions are explicitly listed.
- [ ] Prohibited architecture and scope changes are explicitly listed.
- [ ] The guardrails forbid stack swaps, detection changes, UI, multi-user, extra channels, Celery, Redis, and persistence-model changes.
