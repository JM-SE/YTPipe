# Y02 Broker Production Cutover Specification

**Status:** `draft_pending_human_approval` for this amended closure/canary contract. The original Y02 spec gate was approved 2026-09-05, but that history does not approve this amendment or any new action. This
specification authorizes **no** source change, migration, policy apply,
credential/grant change, deployment, restart, live probe, or service
intervention. It depends on accepted H03 (broker `0eb9ebc`: declarative
compatibility plus explicit output bounds, including the empty-retry persistence
fix) and accepted Y01 (connectivity proven: `synthetic_accepted` plus exactly
one consented URL probe `youtube_accepted`, semantic manifest digest
`sha256:60450bde099909b93a79deb03d07576b47b8282c0e0a4ded91aa56dd1a61615e`).
YTPipe baseline `321c750` is implementation context only and is not Y02
acceptance evidence.

## Context

Y00 kept the runtime direct-only with a dormant broker seam. H03 made consumer
flexibility declarative and verifiable (explicit output bytes, strict
`consumer-compat/v1` manifests, desired/effective checks with exit `0/2/1`).
Y01 proved end-to-end connectivity on a disposable H03 stack without touching
production. Y02 is the production cutover: it moves normal summarization traffic
from the direct path (`DirectSummarizationGateway -> SummarizationService ->
LLAMA_CPP_BASE_URL -> llama-server`) to the broker, by bounded canary with
one-to-one reconciliation, ending with broker as the selected operational/default
route while retaining the direct implementation as a tested, configuration-selectable independent route and explicit rollback. Y02 is NOT a test phase; Y01 was the final connectivity test and is
complete.

YTPipe must remain functional with `SUMMARY_ROUTE=direct` and support `broker` without automatic broker-to-direct fallback. Neither application may require the other to function in its independent mode. `llm-broker` receives no YTPipe-specific logic, fields, contracts, names, or behavior; the integration boundary is only the generic HTTP contract plus configuration/data.

This is not a contract renegotiation (the frozen H03/Y01 profile, limits,
timeouts, and digest are referenced, never redefined), retention hardening,
least-privilege compat role, config v3, public discovery, policy/grant
auto-mutation, retry-after-ambiguity, provider/model selection, Role Radar
integration, third-party docker README/example work, or GitHub-based deployment
(CI is verification-only; release artifacts are operator-deployed by design).

## Requirements

### Paso 1 — Approval with no productive effect

- [ ] Record human approval of this Y02 spec (or an equivalent deploy plan).
  Without it, no step below may start.
- [ ] The operator declares and records before any productive action: the chosen
  broker deploy topology (systemd-native vs compose; topology is operator input,
  deferred by design, not frozen here), the bounded canary scope (channel/count
  plus timebox), the abort criteria, and the operating window.
- [ ] Cheap pre-checks replace a separate full re-validation: `git status` clean
  in both repos, `git show --stat` confirms the exact tree to deploy, and CI is
  green on that exact commit.
- [ ] Full re-validation becomes mandatory only if: CI is red on the deploy
  commit, the tree changed since Y01 acceptance, or the environment drifted. Any
  of those requires fresh recorded evidence before proceeding.

### Paso 2 — H03 broker in prod-like environment with repeated checks

- [ ] Deploy the exact validated H03 binary (same commit as the green CI run) in
  a prod-like environment. No mixed old-binary/new-policy operation is supported.
- [ ] Approved order only: migrate (includes `00005`) -> install H03 binary ->
  add explicit policy (`max_output_bytes=16384`, `max_output_tokens=1024`,
  `max_attempts=1`, `workload_timeout_seconds=300`,
  `response_header_timeout_seconds=60`) -> `policy apply` -> `policy status`
  shows `unchanged` -> restart API and worker only with separate approval.
- [ ] `llm-broker compat desired` and `compat effective` both print `compatible`
  with the same digest `sha256:60450bde...61615e`.
- [ ] Broker `ready`, worker idle, no unresolved indeterminate task.
- [ ] Synthetic probe succeeds (`synthetic_accepted`). The real-URL probe is NOT
  repeated: Y01 already covered it, and repeating it would be duplicate inference
  with no acceptance value.

### Paso 3 — Real-DB migration and canary cutover

- [ ] Back up the production broker database before migrating. Only with explicit
  operational approval: migrate -> install H03 -> explicit policy ->
  apply/status -> restart API/worker.
- [ ] Repeat Paso 2 checks (compat desired/effective, ready/idle, synthetic)
  against production before enabling any traffic.
- [ ] Enable the broker route ONLY for the declared canary scope. All remaining
  traffic stays on the direct path.
- [ ] Reconcile every canary task one-to-one (broker terminal state against the
  YTPipe stage), including whether each success reached Telegram and whether each failure/quarantine produced an explicit sanitized notice. Retained evidence holds only sanitized status/categories and
  the digest — never content, URLs, transcripts, credentials, task IDs, DB
  detail, or backend identity/value.
- [ ] Enforce one invocation per task and workload `max_attempts=1`. Never retry,
  replay/resubmit POST, rotate to a new idempotency key, cancel/delete, fall
  back, restart/recover, schedule, or run background work after ambiguity.
  Resolve any indeterminate task through the supported operator flow before
  another inference.
- [ ] When a broker task ID is returned, YTPipe durably stores the opaque ID and protected logical key before leaving its gateway whenever possible. After post-submit ambiguity it quarantines with zero POSTs/new keys/fallback/restart. Following explicit `llm-broker worker resolve`, YTPipe may reconcile only the existing task through `GET /v1/tasks/{id}/result`; success completes the stage, while failed/cancelled terminalizes with a sanitized reason. If no ID was durably received, quarantine remains operator-controlled with deduplicated escalation and no inferred ID, new key, or automatic resend. IDs/keys never enter Telegram, generic logs, or retained evidence.
- [ ] Abort on: any `broker_protocol_error`, any unresolved indeterminate task,
  `broker_output_incomplete`/`failed` rate above the declared threshold, or
  `policy status` drift. Rollback is deactivating the broker route/flag — the
  direct path stays intact until the final step. Revoke the scoped credential on
  incident. Never auto-delete volumes or data.

### Final step — Broker selection with direct independence retained

- [ ] Only after the canary meets the declared criteria plus operational
  approval: select broker as the operational/default route. Do not remove,
  disconnect, or degrade the direct implementation.
- [ ] Verify post-cutover health with broker serving and `SUMMARY_ROUTE=direct`
  still tested, available, and independently operable as an explicit,
  approved configuration rollback. There is no automatic fallback.

## Verification and Acceptance Criteria

- [ ] Digest-correlated evidence for every step (desired/effective/synthetic/
  canary share `sha256:60450bde...61615e`).
- [ ] `policy status unchanged`, `ready true`, worker idle, and zero
  indeterminates before each activation.
- [ ] Zero broker requests from startup/polling/Telegram outside the canary scope.
- [ ] Observe the canary for at least 12 hours and close only after representative traffic: reconcile all observed broker/YTPipe activity 1:1, confirm Telegram delivery for every success or an explicit notice for every failure/quarantine, meet zero abort criteria, show no policy/digest drift, and resolve every indeterminate. If traffic is insufficient, record `inconclusive/insufficient traffic` and extend only as necessary or use an already-authorized safe functional probe; never substitute elapsed time alone, impose a fixed 48-hour window, or manufacture production indeterminates.
- [ ] Reconstruction acceptance tests prove zero POSTs while quarantined, GET-only reconciliation after operator resolve, correct succeeded/failed/cancelled stage completion, and a blocked no-ID case with one deduplicated alert.
- [ ] `git diff --check` clean; applied migrations verified at the expected version.
- [ ] `@coder-heavy` self-verifies after execution, then invokes a fresh-context
  `post-implementation-reviewer` for architecture/security/acceptance review
  before human acceptance. No visual review required.

## Risks and Stop Conditions

- A 360-second client deadline does not guarantee queue completion; require an
  idle queue before each activation.
- `length` finish or strict-oracle mismatch can fail canary tasks: capture the
  safe category and re-plan; never raise limits silently.
- Nullable legacy fallback, provisioner breadth, and untrusted provider usage
  apply exactly as documented in H03; this spec changes none of them.
- Stop and reopen scope if execution demands retention changes, duplicate
  inference handling, app-specific broker behavior, a public discovery endpoint,
  policy/grant auto-mutation, retries after ambiguity, or any weakening of normal
  submission/worker preflight.
- Every production migration, deployment, restart, or service intervention needs
  separate explicit approval, recorded with the step evidence.

## Handoff

Planner owns this specification. The user must explicitly approve it and switch
manually to `@coder-heavy` for implementation. `@coder-heavy` must self-verify,
then invoke a fresh-context `post-implementation-reviewer` for the required deep
architecture/security/acceptance review, correct blockers, and wait for human
acceptance. No visual review is required.

## Operator Declarations 2026-09-05
- **Deploy topology:** `systemd-native` (operator input decided 2026-09-05, not frozen).
- **Canary scope (amended):** all channels, minimum 12h, subject to representative traffic and the closure reconciliation gate above. The former fixed 48h declaration is superseded. Terminal retries, if separately authorized and policy-safe, are new tasks reusing persisted `Video.transcript` (no re-fetch); indeterminate work is never resubmitted.
- **Abort criteria:** fix-forward recorded below threshold / full rollback above threshold. Thresholds: any `broker_protocol_error`, any unresolved indeterminate task, more than 20% `broker_output_incomplete`-failed, or drift in `policy status`. Indeterminate tasks follow operator resolution plus GET-only reconciliation and never retry with new keys. Any broker/YTPipe code or config change mid-canary restarts the minimum clock and requires Paso 1 re-validation; limits are never raised silently.
- **Operating window:** the 2026-09-05 Paso 2 approval is historical evidence only. This amendment, implementation, migration, deploy, restart, route change, and any fresh canary each require explicit approval; the readiness discussion did not grant it.

## Design Constraint 2026-09-05 — Agnostic Route Resolver
- Per-channel `if`/branching in routing code is **forbidden**. No channel names or channel lists in routing code.
- Inference route (`direct` vs `broker`) is resolved by an **agnostic resolver** reading **data** (channel/workload configuration). Absent data defaults to `direct`.
- Canary scope and rollback are **data changes only** (no deploys, no new branches): enabling traffic sets route to `broker`; rollback sets route back to `direct`.
- The existing `recovery_target` (`direct_llama` / `none`) governs post-failure recovery only and is not an inference-route selector.
- This criterion is frozen input for the 3b work, which still requires its own spec + implementation + review + operational go before any real traffic.

## Interim 2026-09-07 — ResponseHeaderTimeout 60-240
- **Change:** live broker `response_header_timeout_seconds` raised `60` → `240` on 2026-09-07 (interim, operator-approved). Backend field only; no migration, no policy apply, no code change.
- **Rationale:** worst observed prefill ~58s for a ~10.7k-token prompt; `240` is ~4x observed and stays below the `300`s workload timeout.
- **Evidence:** `policy status` unchanged=1 unmanaged=52 topology-mismatch=0; services restarted active; `:80` health ok; `/ready` true; worker `no unresolved ambiguous executions`; compat desired+effective both `compatible` with digest `sha256:60450bde099909b93a79deb03d07576b47b8282c0e0a4ded91aa56dd1a61615e`.
- **Written residual:** prompts whose first response header exceeds `240`s still go indeterminate (worker blocks, YTPipe circuit pauses) → owned by the future structural phase (streaming progress deadlines or derived budgets + compat finding + YTPipe sticky-pause decoupling).
- **Rollback:** `/etc/llm-broker/config.json.pre-interim` on host restores `60` (then restart api+worker).

## Follow-up Requirements 2026-09-07 (post-canary; each needs design plus spec plus review; no hot changes during canary)
- **R3 — No global pause on a single failure:** one video's summarization failure must not halt all summaries; the circuit must isolate the failing video (per-video pending/retry) while the rest keeps flowing.
- **R4 — Failure notice must state WHY:** the per-video Telegram notice must include the specific sanitized failure cause (for example broker timeout versus output too long versus no transcript), not only the generic message; failure codes must be journal-observable as well as persisted.
