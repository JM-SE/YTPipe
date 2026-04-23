# Execution Workflow Specification

## Context
This document defines how the implementation agent should execute the approved MVP plan. It complements `specs/agent_guardrails.spec.md` and the numbered phase specs in `specs/implementation/`.

## Requirements
- [ ] Implementation proceeds one numbered phase at a time.
- [ ] `@tech-lead` remains the single orchestration owner for implementation work.
- [ ] Secondary agents and skills may be used only as controlled support.
- [ ] Each phase ends with verification, review, and human approval before the next phase begins.
- [ ] Each implemented phase ends with a required handoff that includes step-by-step local testing guidance and a short manual testing checklist whenever local verification is possible.
- [ ] If a phase cannot be fully tested locally, the handoff must state what can be tested locally, what cannot, and the exact missing prerequisite.
- [ ] Spec gaps discovered during execution must be resolved in docs before implementation continues.

## Technical Approach
Execution follows `specs/implementation/00_implementation_phase_index.spec.md` as the ordered plan. The active phase spec is the immediate work contract, while the docs in `specs/` remain the project source of truth.

`@tech-lead` owns implementation, integration, and final decisions within the approved guardrails. Secondary agents may be called for specialized slices such as code review, tests, debugging, refactors, documentation, or security checks. Skills may be loaded when they directly match the active phase technology or validation need.

Delegation must stay narrow. Secondary agents and skills support the current phase; they do not redefine architecture, scope, or persistence. No workflow may batch multiple phases into a single autonomous pass.

## Implementation Steps
1. Select the next incomplete phase from `specs/implementation/00_implementation_phase_index.spec.md`.
2. Implement only the work defined in that phase spec.
3. Use relevant skills and secondary agents only when they improve implementation or review quality for the active phase.
4. Run the most relevant checks for the phase and capture the result.
5. Perform a review pass using an appropriate agent or skill when the phase complexity justifies it.
6. Prepare the per-phase handoff with step-by-step local testing instructions, a short manual testing checklist, and any explicit note about missing prerequisites for non-local verification.
7. Present the completed phase for human validation.
8. Start the next phase only after the current phase is accepted.

## Acceptance Criteria
- [ ] The execution policy names `@tech-lead` as the sole orchestrator.
- [ ] The workflow requires one-phase-at-a-time execution.
- [ ] The workflow permits secondary agents and skills only as controlled support.
- [ ] Verification and review are required at the end of each phase.
- [ ] The per-phase handoff requires local testing guidance and a short manual checklist whenever local verification is possible.
- [ ] When local verification is incomplete, the handoff must identify the locally testable portion, the non-local portion, and the exact missing prerequisite.
- [ ] Human approval is required before moving to the next phase.
