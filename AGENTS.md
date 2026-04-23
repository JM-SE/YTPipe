# Repository Agent Policy

## Purpose
This repository uses a controlled, phase-based implementation workflow. Agents and skills are allowed, but only under a single orchestrator so execution stays aligned with the approved specs.

## Source Of Truth
- Product, architecture, data model, polling, retry, and guardrail decisions live in `specs/`.
- Phase execution order lives in `specs/implementation/00_implementation_phase_index.spec.md`.
- A phase may not advance until its own acceptance criteria are met and the result is reviewed.

## Execution Ownership
- `@tech-lead` is the primary implementation and orchestration agent for this repository.
- Secondary agents and skills may be used only through `@tech-lead`.
- No secondary agent may redefine architecture, scope, persistence, or operational contracts.

## Required Workflow
1. Implement one phase at a time following `specs/implementation/` in order.
2. Keep implementation bounded to the current phase scope.
3. Run the most relevant verification for the completed phase.
4. Perform an agent or skill-based review at the end of the phase when it adds value.
5. Present the phase result for human review before starting the next phase.

## Agent And Skill Usage
- Use repository specs first; do not let agents infer missing product requirements from scratch.
- Use secondary agents for specialized tasks such as test writing, refactor review, debugging, documentation, or security review when they improve quality.
- Use relevant skills when the active phase clearly matches them.
- Keep delegation narrow: delegate a slice of work, then integrate the result back under `@tech-lead` ownership.

## Prohibited Behavior
- Do not implement multiple phases in one uncontrolled pass.
- Do not skip acceptance criteria or review gates.
- Do not replace the approved stack.
- Do not change the uploads-playlist detection strategy.
- Do not add UI, multi-user support, extra notification channels, Celery, Redis, or unapproved persistence changes.

## Review Gates
- Every phase ends with verification and review.
- Human approval is required before starting the next numbered phase.
- If a phase reveals a spec gap, stop and update the specs before continuing.
