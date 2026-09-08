# Y02c Resilient Per-Video Recovery Specification

**Status:** `approved` (human implementation acceptance granted). This is a planning artifact only: it authorizes no source change, migration, deployment, restart, route change, canary, live probe, or service intervention. Each remains separately human-approved.

## Context

Y02's abort/canary rules, code/config clock-reset rule, 240-second interim timeout residual, and follow-ups R3/R4 are recorded in `Y02_broker_production_cutover.spec.md:133-155`. This safe-stabilization prerequisite restores independent direct-runtime recovery while ensuring one failing video never pauses the queue and every terminal failure has a sanitized, specific Telegram notice.

Current coupling is unsafe: a local no-pause patch can disconnect pause-gated restart/cooldown/alerts; exception paths can bypass exhaustion and due-time backoff; legacy `paused=true` can block summaries; broker error text loses class/code; and terminal broker-output failures can repeatedly replay one task/key. Manual Telegram summaries also need an eventual terminal reply/delivery.

YTPipe remains independently functional with `SUMMARY_ROUTE=direct` and also supports `broker`. The exact Y02b resolver contract remains: `SUMMARY_ROUTE` absent/unrecognized defaults to `direct`; there is no automatic broker-to-direct fallback. `llm-broker` receives no YTPipe-specific logic, fields, contracts, names, or behavior: integration is only through its generic HTTP contract plus configuration/data. This is not the deferred structural rewrite: no cross-process lease claim, Telegram outbox, attempt-history table, or broker streaming/progress deadline work.

## Dependencies / Inputs

- [ ] Y02/Y02b remain the governing route/canary contracts; direct is an independently supported runtime mode.
- [ ] Current evidence: `app/services/pipeline.py`, `polling.py`, `llama_recovery.py`, `telegram_command_queue.py`, broker gateway/error/traffic adapters, `PipelineStage`, migrations, and their tests.
- [ ] Durable-state finding: `PipelineStage` currently has only attempt count, last attempt, and free-text error; it lacks due time, typed error, quarantine, and broker correlation state. `SyncState.state_metadata` is process-level and cannot safely represent per-video state. An additive Alembic migration is therefore required; do not assume existing fields suffice.

## Files / Modules

- [ ] `app/services/summary_failure_policy.py` — pure typed outcome, classification table, retry/backoff and legacy-state policy.
- [ ] `app/services/summary_recovery_coordinator.py` — direct-only incident/cooldown/probe decisions and persistence-facing transition inputs; no HTTP, Telegram, or stage traversal.
- [ ] `app/services/{broker_gateway,broker_errors,broker_traffic}.py` and direct gateway adapter — map provider results/exceptions to typed inputs; preserve broker correlation only internally.
- [ ] `app/services/pipeline.py` — thin per-video stage orchestration and typed transitions only.
- [ ] `app/services/polling.py` — load/save the direct incident, schedule only due stages, request a bounded probe, and continue other videos.
- [ ] `app/services/telegram_failure_notice.py` plus `telegram_command_queue.py` / Telegram delivery adapter — sanitized notice formatting and durable eventual terminal reply delivery.
- [ ] `app/models/pipeline_stage.py`, relevant `SyncState` access, and one new Alembic revision — additive minimal durable state and compatible upgrade/downgrade.
- [ ] Focused tests in pipeline, polling, broker-route, recovery, and Telegram-command suites; add focused policy/coordinator tests rather than a god-service test fixture.

## Module Boundaries / Contracts

- `SummaryFailureOutcome` is an immutable value object containing: route; stable failure class and code; disposition (`terminal`, `retry`, `quarantine`); `retry_at` or delay; recovery owner/action; sanitized display reason; and protected opaque broker task/logical-key correlation when known. Adapters supply structured facts; policy never parses provider error text to decide behavior.
- The policy maps named constants/configuration to outcomes. It owns classification, bounded retry delay, exhaustion, and user-safe copy keys. It receives a clock; its table is unit-testable.
- The coordinator owns only a direct-runtime incident/circuit: cooldown check, at-most-one direct llama restart per cooldown, and one controlled recovery probe. It receives clock and restart interface. Broker outcomes always have recovery owner `operator`/action `none` and cannot reach this coordinator.
- Pipeline owns dependency ordering and durable per-video transitions. It must set `next_attempt_at` for retries before return, terminalize exhausted work on every exception path, and never use a global pause as a prerequisite for video progress or recovery.
- Polling owns process incident load/save and due-work selection, not failure classification. Telegram formatting owns sanitized text; transports own send/retry mechanics. Do not merge these roles into `PipelineService` or `YouTubePollingService`.

## Extraction Plan

- Name injected policy/configuration for retry schedule, maximum attempts, direct circuit cooldown, probe limit, and alert suppression; inject clock, restarter, gateway transport, and Telegram transport.
- Add `PipelineStage` columns for `next_attempt_at`, `failure_class`, `failure_code`, `quarantined_at`, and minimal safe broker correlation/quarantine metadata. `last_error` remains sanitized display text, not a classifier. Add a due-work index if query-plan verification shows it is needed.
- Keep broker task IDs/idempotency keys out of Telegram, generic logs/journals, and retained Y02 evidence. After a broker submission returns a task ID, persist that opaque ID and the protected logical key durably before the gateway returns whenever possible. Store only the minimal correlation required for operator resolution; never expose, rotate, or guess a key.

## Modularity Risks

- [ ] Do not turn `PipelineService`/`YouTubePollingService` into a classifier, circuit breaker, persistence repository, and Telegram sender. Keep policy pure, recovery side effects behind an injected interface, and adapters/formatters isolated.

## Technical Approach

1. **Separate state.** Each summary stage independently becomes completed, due-retry, terminal, or quarantined. A direct dependency incident is separately represented in existing summarization `SyncState` metadata only after validating a versioned, direct-only schema. It is not a queue pause and cannot represent broker recovery.
2. **Direct path.** Classify transient direct runtime failures as a due retry for only that video. If systemic, open/update the direct-only incident, emit one controlled alert, and permit at most one `llama-server` restart per configured cooldown followed by one controlled probe. Other videos remain eligible; recovery success clears only the direct incident.
3. **Broker path.** Permanent invalid request/config/policy failures terminalize. Retry is allowed only when the broker result explicitly confirms it is safe. Terminal output-invalid/incomplete stops the same task/key loop. A timeout or post-submit ambiguity enters durable conservative quarantine: no inference POST, new key, direct fallback, llama restart, or automatic resubmission. If a task ID was persisted, YTPipe waits for explicit operator resolution through `llm-broker worker resolve`, then reconciles only that task via `GET /v1/tasks/{id}/result`, either automatically with bounded backoff or by explicit manual trigger, and never by POST. `succeeded` completes the stage from that result; `failed`/`cancelled` terminalizes it with a sanitized reason. If no task ID became durable because ambiguity preceded `Location`, quarantine remains operator-controlled with a deduplicated alert and documented manual escalation/resolution; YTPipe must not infer an ID, create a key, or resend automatically.
4. **Compatibility.** Treat legacy `paused=true`, missing/invalid historic recovery targets, and unknown incident schema as non-authoritative for direct recovery: clear/ignore the queue-blocking effect safely, retain sanitized evidence, and require explicit current direct-route evidence before any restart. Never default historic broker/unknown state to `direct_llama`; no automatic database recovery may contradict Y02.
5. **Telegram.** Terminal automatic-video failures create one sanitized specific failure notice; retry/quarantine notices are deduplicated by durable state. Manual `/summary` requests reuse their existing durable reply queue so a temporary Telegram failure retries to one eventual terminal reply, without creating duplicate sends.

## Implementation Steps

1. Inventory current stage, summary `SyncState`, and command-reply schemas; define additive migration and a backward-compatible reader for rows predating it. Do not mutate legacy rows into a recovery target during migration.
2. Implement the pure outcome/policy and formatter with named codes (including no-transcript, direct timeout/transport, broker invalid/config/policy, output-invalid/incomplete, safe-retry, and indeterminate) and sanitized copy.
3. Adapt direct and broker gateways to emit structured facts/outcomes; persist returned broker correlation before leaving the gateway when possible, preserve Y02b route resolution and fail-closed broker construction, and remove error-text-based policy decisions.
4. Add the focused recovery coordinator and wire polling/pipeline transitions so due time and exhaustion are honored on normal and exception paths. Replace global pause gating with per-video eligibility plus the direct-only incident.
5. Wire durable notices and manual reply delivery; add attribution/journal fields using stable class/code without prompts, summaries, credentials, or raw broker IDs.
6. Execute verification, then conduct the required fresh-context review and obtain human implementation acceptance before any separately approved deployment/canary action.

## Edge Cases

- [ ] A failed video A must not block video B's summary or Telegram delivery, including when A is due for delayed retry.
- [ ] A terminal broker output error cannot re-POST or endlessly poll/replay the same task/key.
- [ ] Reconstruction after an indeterminate broker result preserves quarantine and produces neither POST/new key nor fallback/restart; operator-resolved tasks are reconciled by GET only.
- [ ] An ambiguity without a durable task ID remains blocked under operator control and emits one deduplicated escalation alert rather than guessing or resubmitting.
- [ ] Legacy paused or malformed recovery metadata cannot restart llama or make broker work appear direct.
- [ ] Failure display text is fixed, sanitized, and code-derived; provider exception text is not sent to Telegram or used as policy input.

## Verification Plan

### A. Focused unit tests

- [ ] Test adapter-fact-to-outcome mapping, stable code/class, no-text parsing, retry/exhaustion due-time calculation, and sanitized notice formatter.
- [ ] Test direct restart cooldown at before/equal/after boundary, one probe, and broker outcomes never invoking the restarter.
- [ ] Test legacy paused/missing/invalid recovery target handling, including no implicit direct target.

### B. SQLite/mock-transport integration

- [ ] Direct failure for video A leaves video B to summarize and Telegram-deliver; A is due-retried, one restart maximum occurs per cooldown, then A recovers and delivers.
- [ ] Classified terminal broker failure isolates A, preserves code/reason, never controls llama, and B completes.
- [ ] Broker indeterminate creates durable quarantine; after a new process/session instance, assert zero inference POSTs, new keys, fallbacks, or restarts while quarantined.
- [ ] After explicit operator resolution, reconstruction uses only `GET /v1/tasks/{id}/result`: `succeeded` completes the existing stage with its result, while `failed`/`cancelled` terminalizes it with a sanitized reason and neither path issues a POST.
- [ ] When no task ID was durably received, reconstruction remains blocked, sends one deduplicated operator alert, and requires documented manual resolution without creating a key or request.
- [ ] A manual Telegram reply has one transport retry then one eventual delivery, with no duplicate terminal reply.

### C. Full local verification

- [ ] Run focused suites, then `python -m pytest` from an isolated test copy that excludes `.env`; verify no local canary route or credentials are read.
- [ ] Required Docker Python 3.13 format (values shown are non-secret):
  ```sh
  test_root="$(mktemp -d)" && \
  tar --exclude=.git --exclude=.env --exclude=.venv -C "$PWD" -cf - . | tar -C "$test_root" -xf - && \
  docker run --rm -v "$test_root:/work" -w /work \
    -e APP_ENV=local -e SUMMARY_ROUTE=direct -e TELEGRAM_COMMANDS_ENABLED=false \
    -e TELEGRAM_NOTIFICATIONS_ENABLED=false -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.13-slim sh -ec 'python -m pip install -e ".[dev]" && python -m pytest && python -m compileall -q app' ; \
  status=$?; rm -rf "$test_root"; exit "$status"
  ```
- [ ] Run `git diff --check` and inspect `git status --short`. Clean generated `ytpipe.egg-info` only after confirming it is test-generated and not a pre-existing user edit; do not blanket-delete unrelated work.

### D. Post-approved operator validation

- [ ] Only after an explicit deployment/restart approval: run `/summary <youtube-url>` for one success and, only in preproduction, one controlled failure; inspect sanitized route attribution/failure logs and durable state without secrets.
- [ ] Run scheduled/manual polling with at least two eligible videos; confirm worker ready/idle, no unresolved indeterminates, broker failures do not restart llama, and direct restart count does not exceed cooldown.
- [ ] After separate explicit approval, begin a fresh Y02 canary and observe for at least 12 hours. Closure also requires representative traffic and one-to-one reconciliation of all observed broker/YTPipe activity: Telegram delivery for every success or an explicit notice for every failure/quarantine, zero abort criteria, no policy/digest drift, and zero unresolved indeterminates. If traffic is insufficient, record `inconclusive/insufficient traffic` and extend only as necessary or run only an already-authorized safe functional probe; do not impose a fixed 48-hour window or manufacture indeterminates in production.

## Rollback Plan

- [ ] With separate human approval, perform the data-plane rollback `SUMMARY_ROUTE=direct`; retain all broker task/quarantine evidence and do not delete data. Direct remains supported.
- [ ] Use an additive, code-rollback-compatible migration: old code must tolerate added nullable state. Do not downgrade/delete durable quarantine automatically; any database rollback needs separate approval and evidence.

## Correction Gate — Diff Review Findings

**Status:** `changes_required`; **required before human implementation acceptance or canary.** The existing implementation is not accepted. This gate authorizes no operational action, and no canary readiness may be claimed until every item below passes a fresh review.

- [ ] Preserve validated broker taxonomy end-to-end: extend the typed broker result/error representation so `BrokerSummarizationError` and stage `failure_class`/`failure_code` retain broker `error.class` and `error.code`. Do not map terminal failures generically or parse provider text; `indeterminate` is always `quarantine`.
- [ ] Create the deterministic logical idempotency key before POST and persist it on the stage before every submission attempt. A pre-`Location` transport/deadline ambiguity quarantines with that key and no task ID; it must never POST/replay/create a key/fallback/restart. After validated `Location`, persist task ID and key before polling; every subsequent timeout/protocol/transport ambiguity retains both. Keep both opaque: never expose them in Telegram, generic logs, or retained Y02 evidence.
- [ ] Add durable reconciliation state: `reconciliation_status`, `next_reconcile_at`, and deduplicated alert/reason fields. Quarantine is `awaiting_operator_resolution` by default; only an explicit YTPipe operator action/controlled trigger, backed by `llm-broker worker resolve` evidence, may make a known-ID stage GET-eligible. Then reconcile only by bounded-backoff GET: `pending` remains GET-only, while `succeeded`, `failed`, and `cancelled` terminalize correctly. No-ID ambiguity remains awaiting operator resolution, sends one actionable deduplicated alert, and cannot infer an ID or transition itself.
- [ ] Use one canonical notice path: wire `format_summary_failure_notice(SummaryFailureOutcome)` into the existing durable automatic fallback/Telegram queue, or remove/avoid the duplicate API. Notices must be code-derived, specific, sanitized, durable/eventually delivered, and deduplicated for terminal and quarantine outcomes; raw exception text is prohibited.
- [ ] Extend and pass focused evidence: mock-HTTP classified broker `failed`/`indeterminate`; pre-`Location` ambiguity persists key/no ID and has zero later POST; post-`Location` timeout retains both; reconstruction proves no GET before explicit transition, then GET-only bounded backoff; no-ID yields one alert; terminal Telegram reasons contain no sensitive values; and direct/broker agnosticism holds. Run the isolated Docker suite, `compileall`, `git diff --check`, and clean-install/migration evidence; remove `build/` or egg-info only after confirming it was generated and is not user work.
- [ ] Preserve all Y02c constraints: direct stays independent/configurable; broker never restarts llama or falls back to direct; this correction performs no operational action. After self-verification, obtain the mandatory fresh-context review and correct its blockers before human acceptance.

## Correction Gate Addendum — Manual Flow and Typed Direct Facts

**Status:** `changes_required`. This addendum blocks human implementation acceptance and canary pending fresh-context review. It authorizes no deployment, restart, migration, or canary.

### Required Contracts and Boundaries

- [ ] A quarantined manual `/summary` produces one explicit, durable command-reply lifecycle outcome using sanitized, code-derived text; it must not remain in a 30-second infinite pending retry. It uses existing command-reply delivery retries if delivery fails, and must not create automatic fallback-notification stages, infer/replay broker work, create a key, submit a request, or fall back to direct.
- [ ] Expose `request_quarantine_reconciliation` only through a real authenticated/admin, auditable operator invocation boundary after evidence of `llm-broker worker resolve`. `broker_resolved` is not caller-asserted trust: record a minimal resolution reference and/or confirmed operator action in durable audit state. Only a known-task-ID quarantine may transition to `get_eligible`; a no-ID quarantine remains operator-blocked and has exactly one durable, deduplicated escalation notice.
- [ ] The operator boundary owns authorization, evidence/reference validation, audit recording, and the explicit transition. Pipeline/polling consume only the persisted transition; polling performs bounded GET-only reconciliation after that transition and never before it.
- [ ] The direct gateway adapter maps timeout, transport, non-200, invalid envelope, and empty response to structured stable `failure_class`/`code` facts. `PipelineService` and policy must not inspect `str(exc)` to select failure policy; all persisted/displayed errors remain sanitized.
- [ ] Preserve broker `transient_unsent` and `transient_safe` unchanged in `PipelineStage.failure_class` while applying their local retry disposition. An unknown broker class still quarantines.

### Focused Verification and Acceptance

- [ ] Test manual Telegram command lifecycle: quarantine yields one durable sanitized reply, no 30-second pending loop, no automatic notification stage, no broker replay/fallback, and existing command-reply delivery retries handle send failure.
- [ ] Test the authenticated/admin operator caller: resolution evidence/reference is audited, caller-supplied `broker_resolved` alone is rejected, only known-ID stages become `get_eligible`, and no-ID stages remain blocked with one durable escalation across reconstruction.
- [ ] Test that post-transition reconciliation is GET-only (including `pending` bounded backoff) with zero GET before transition and zero POST/new key/fallback/restart in either quarantine state.
- [ ] Test direct typed mappings for timeout, transport, non-200, invalid envelope, and empty response, asserting stable class/code, sanitized text, and no exception-string policy branching.
- [ ] Test broker `transient_unsent` and `transient_safe` persistence plus local retry behavior; assert unknown class quarantines.

## Correction Gate Addendum — Crash-Safe Broker Submission and Acceptance Evidence

**Status:** `changes_required`; this addendum supersedes earlier references that imply YTPipe queries or executes `llm-broker worker resolve`. It authorizes no deployment, restart, production migration, canary, commit, or other operational action.

### Crash-Safe Submission Contract (BLOCKER)

- [ ] Make broker submission an explicit, persistence-owned state machine: `prepared` durably stores the deterministic idempotency key and a conservative pre-submit marker; `accepted` validates `Location` and atomically stores the task ID and same key before any result GET; only then may `polling`/`reconcile` occur.
- [ ] Change the broker adapter to return an accepted-task handle/result, or use a callback-free submit-then-poll contract. It must not hide POST plus polling, perform session I/O, or own durable transitions.
- [ ] A pre-`Location` timeout/transport ambiguity must durably quarantine key-without-ID and block all automatic POST/replay/new-key activity. A post-`Location` timeout/protocol/transport failure must durably quarantine both key and task ID, block new POST, and allow future GET only after an explicit operator transition. Opaque IDs/keys are prohibited in user messages and logs.

### Operator Reconciliation Audit Boundary

- [ ] The authenticated admin reconciliation endpoint must accept only a known-ID stage currently quarantined as awaiting operator resolution. It records an auditable actor identity/source (when available), normalized bounded nonblank resolution reference, timestamp, stage ID, and transition outcome.
- [ ] Reject blank references, unauthenticated/invalid callers, non-quarantined or wrong-stage/wrong-user records, and no-ID records (409). Do not query or execute `llm-broker worker resolve`: the operator confirms resolution through the bounded reference. A successful action sets `get_eligible`; subsequent reconciliation is GET-only.

### Required Focused Integration Evidence (BLOCKER)

- [ ] Mock HTTP proves accepted POST persists task/key before the first GET; after process reconstruction there is zero new POST and only GET after explicit transition. Pre-`Location` timeout/transport persists key/no ID and remains blocked; post-`Location` timeout/protocol/transport persists both, has zero later POST, and is GET-only after transition.
- [ ] API tests for `/internal/reconcile-quarantined-summary` cover missing/invalid auth, blank reference, non-quarantine/wrong stage/user, no-ID 409, and known-ID success with all audit fields persisted.
- [ ] Manual Telegram command integration proves quarantined `/summary` terminalizes immediately once, makes no second content-process attempt or 30-second loop, creates no fallback stage, and retries failed reply delivery until exactly one canonical sanitized message is sent.
- [ ] Gateway-to-pipeline tests retain exact `transient_unsent`/`transient_safe` `failure_class`, `pending_retry`, due time, and opaque correlation. Direct typed mapping tests cover timeout, transport, HTTP, invalid envelope, and empty response through persisted stage and sanitized notification.

### Boundaries and Verification

- [ ] Preserve direct/broker agnosticism, never broker-to-direct fallback, no llama restart for broker, and the existing migration unless a new additive column is truly necessary. Do not place state-machine, HTTP-client, DB, API-auth, or Telegram-formatting responsibilities in `PipelineService`.
- [ ] Before any human acceptance or canary, use an isolated Docker clean copy with explicit suitable test-DB configuration; run full `pytest`, `compileall`, `git diff --check`, and migration upgrade/packaging checks; then require a mandatory fresh-context post-implementation review. No human acceptance/canary proceeds until this section passes.

## Acceptance Criteria

- [ ] R3: one video failure cannot globally pause summaries; retry is per-video and due-time/exhaustion safe.
- [ ] R4: terminal failure notices include a sanitized specific reason, while stable class/code are persisted and journal-observable.
- [ ] Direct recovery is autonomous but direct-only, cooldown-limited, and probe-bounded; broker failures never restart llama or fall back to direct.
- [ ] Broker terminal and indeterminate handling satisfies Y02 non-replay/non-new-key rules, with durable operator-resolved quarantine.
- [ ] Process reconstruction proves GET-only reconciliation after `worker resolve`, zero POSTs in quarantine, correct success/failure terminal transitions, and blocked deduplicated escalation when no durable task ID exists.
- [ ] Correction Gate — Diff Review Findings is fully passed: broker taxonomy/correlation/reconciliation/notice-path requirements and their focused verification evidence are reviewed fresh; until then, the implementation remains `changes_required` and cannot be accepted or called canary-ready.
- [ ] Legacy pause/recovery metadata cannot create a sticky queue pause or an unsafe direct restart.
- [ ] `@coder-heavy` self-verifies, then invokes a fresh-context `post-implementation-reviewer` for architecture, security, and acceptance scope; correct blockers before human acceptance. No visual review is required.
- [ ] Human implementation acceptance and every deployment, restart, route change, and canary remain separately approved.

## Post-Implementation Follow-ups (Optional Hardening, Deferred)

Recorded from the approved fresh-context review as `approve with follow-up`. Not blocking; defer until Y02c operates correctly in production. Apply independently if/as needed.

- [ ] (Optional) `summary_failure_policy.py:63-68` maps broker output-validation failures (`broker_output_invalid`/`broker_output_incomplete`) to `failure_class="permanent"`. Consider a dedicated `failure_class="output_invalid"` for clearer taxonomy. No correction-gate impact: `failure_code` remains preserved and no broker `error.class` exists in that path.
- [ ] (Optional) `_attempt_broker_summary_stage` returns `summary_attempted=True` even when a crash-residue quarantine skips all POST, inflating `stages_processed` and triggering the between-video drain pause (`pipeline.py:966`, `pipeline.py:596-598`). Observability/timing only; no correctness regression.
- [ ] (Optional) Regenerate/clear the untracked root-owned `build/` artifact before packaging: `build/lib/app/services/broker_gateway.py` diverges from `app/` (older closed allow-list taxonomy). Risk only if a wheel/sdist is built from `build/`. Do not delete unrelated user work.
