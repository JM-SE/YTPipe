# Y02d — Safe New Broker Submissions After Confirmed Transient Terminal Failures Specification

**Status:** proposed implementation handoff. This specification authorizes no production mutation. Deployment, migration, canary, rollback, and remediation of historical stages each require separate operator approval.

## Context

Production YTPipe uses the `broker` route and H02 is healthy. In the 24-hour evidence ending 2026-09-09, stage `4B4R2T4w7Kg` exhausted 3/3 retries and failed despite a transcript and broker `transient_safe` / `backend_error`; `QSxghp_19TY` was `pending_retry` after safe failures. The broker recorded exactly one real task attempt for each terminal task and then marked it failed. The `batch-summary` broker policy deliberately has `max_attempts=1`, and `app/contracts/broker/y01.consumer-compat.json` deliberately sets `max_acceptable_attempts=1` and overall deadline `360`.

`SummaryGatewayContext` currently derives its idempotency key solely from stage and operation identity. A later YTPipe stage retry therefore replays the known terminal task rather than submitting a new inference. Y02c's PREPARED/accepted crash-safety contract, conservative quarantine, typed broker taxonomy, no broker-to-direct fallback, and Telegram canonical-reply behavior remain governing constraints.

This change is limited to a **known broker terminal** result received with task ID and class `transient_safe` or `transient_unsent`, while the YTPipe stage itself retains retry budget. It is not a retry-policy, broker-policy, manifest, direct-route, or historical-stage remediation change.

## Requirements

- [ ] Add a durable retry-generation discriminator to `pipeline_stages`: `broker_submission_epoch` (or an equally explicit name), non-null, zero-default, and bounded by the stage's configured maximum attempts. Existing rows must initialize to `0`. It counts **new broker submission generations**, never broker inference attempts.
- [ ] Preserve the byte-for-byte legacy idempotency key derivation for epoch `0`, including existing PREPARED/accepted crash residues. For epoch `>0`, deterministically derive a distinct key from stage ID, operation identity, and epoch using an unambiguous tagged encoding; do not concatenate ambiguous raw values.
- [ ] On a received terminal broker failure, schedule a new generation only when all are true: route is broker; a task ID is durably known; received class is exactly `transient_safe` or `transient_unsent`; terminality is confirmed; and `attempt_count < max_attempts` under the existing stage policy.
- [ ] Atomically, in the transaction that records that confirmed safe-terminal outcome and `pending_retry` scheduling, increment the epoch once and clear only the **current** broker task correlation needed to make the following retry a new submission. Preserve historical terminal class/code/reason/audit evidence; do not overwrite or conflate it with the next generation's correlation.
- [ ] On the next due stage attempt, derive and persist the new epoch key plus PREPARED state with no task ID, commit, then POST. A returned/accepted task must be persisted atomically as accepted before any GET polling; it is GET-polled only thereafter.
- [ ] An interrupted or ambiguous pre-`Location` POST remains quarantine-only, with no blind POST, replay, new generation, direct fallback, or inferred task ID. Post-`Location` transport/protocol ambiguity remains conservative quarantine/GET-only under the existing Y02c operator workflow.
- [ ] Never increment/create a generation for indeterminate, transport, timeout, pre-`Location`, protocol, policy/client/backend rejection, output-invalid/incomplete, cancellation, unknown class, or stage retry exhaustion. Preserve their existing terminal/quarantine behavior.
- [ ] Keep the public generic broker contract and direct-route behavior stable. Add only a narrowly-scoped optional context field if required to carry the epoch; do not add YTPipe-specific behavior to `llm-broker`.
- [ ] Do not change the broker `batch-summary` policy, its `max_attempts=1`, the consumer compatibility manifest, direct fallback behavior, or historical stages `4B4R2T4w7Kg`, `QSxghp_19TY`, and earlier quarantines. Those stages require separately authorized remediation after rollout.

## Technical Approach

### State and module boundaries

| Area | Responsibility | Constraints |
| --- | --- | --- |
| New Alembic revision and `app/models/pipeline_stage.py` | Add/model `broker_submission_epoch`. | Additive, server default `0`, non-null after backfill; migration check enforces non-negative and upper bound compatible with stage retry ceiling (for example, `epoch <= max_attempts`), so generation cannot grow unbounded. |
| `app/services/broker_idempotency.py` (new, or focused equivalent) | Pure tagged key derivation and epoch validation. | Epoch 0 calls/retains the legacy derivation exactly; epoch >0 uses a versioned/tagged canonical encoding. No persistence, HTTP, or failure-policy rules. |
| `app/services/summarization_gateway.py` / `SummaryGatewayContext` | Carry optional epoch/key derivation input to the broker route. | Epoch defaults to `0`; direct callers and generic public contracts remain compatible. |
| `app/services/broker_safe_retry.py` (new, or focused equivalent) | Pure predicate/transition input: whether a received outcome permits a new generation. | Named classes only (`transient_safe`, `transient_unsent`), confirmed terminal, known task ID, broker route, and remaining stage budget. No string parsing, hashing, DB, or network calls. |
| `app/services/broker_submission.py` / `BrokerSubmissionCoordinator` | Durable PREPARED → POST → accepted/poll lifecycle and atomic safe-terminal transition. | Coordinate transitions only; delegate derivation/predicate. Do not become a policy, migration, hashing, or HTTP catch-all. |
| `app/services/summary_failure_policy.py` and `app/services/pipeline.py` | Preserve typed classification, due-time/exhaustion handling, and thin stage orchestration. | Feed the coordinator typed terminal facts; do not recreate classification rules inline. |

### Generation lifecycle

1. **Epoch zero compatibility.** A new/legacy stage starts at `broker_submission_epoch=0`; derive exactly the pre-Y02d logical key. Existing PREPARED, accepted, quarantined, and terminal residues retain their stored epoch-zero key and current semantics.
2. **Submit/poll.** Before each POST, persist epoch-specific key and PREPARED/no-task-ID correlation and commit. POST once. Validate `Location`, persist accepted task ID/key before result GET, and poll only that accepted task.
3. **Confirmed safe terminal.** When GET returns a terminal failed task with the received typed class `transient_safe` or `transient_unsent` and code such as `backend_error`, call the pure eligibility helper with current route, task ID, and stage budget. If eligible, atomically: retain terminal audit facts, increment epoch once, clear current task correlation, set existing retry scheduling/status, and commit. The increment occurs before the next PREPARED commit, not while polling and not on every loop.
4. **Next scheduled retry.** The ordinary due-stage flow sees the higher epoch, persists its distinct deterministic key and PREPARED/no-task-ID state, commits, then performs one new POST. This makes one new broker task; it is not an additional attempt on the old broker task.
5. **All other terminal/ambiguous outcomes.** Leave epoch/correlation behavior unchanged and follow Y02c terminal/quarantine paths. In particular, a no-ID/pre-`Location` interruption has no safe-terminal evidence and stays blocked rather than becoming a new generation.

## Implementation Steps

1. Inventory the current Y02c schema/revision, `PipelineStage` correlation fields, coordinator transaction boundaries, `SummaryGatewayContext` key compatibility tests, and broker failure facts. Record the exact current legacy epoch-zero key fixtures before changing derivation.
2. Add one additive Alembic revision after `20260907_0011` and the model mapping. Upgrade must initialize existing rows to `0`, then enforce non-null/default/check bound; downgrade must be assessed for compatibility before use and must not mutate historic stage outcomes/correlation.
3. Extract the pure epoch key derivation/validation module and safe-terminal eligibility helper. Make epoch zero a dedicated compatibility branch; document the tagged epoch-positive format and centralize maximum/bound validation.
4. Add an optional epoch input through `SummaryGatewayContext` only where broker key construction needs it. Keep direct callers, epoch-zero serialized behavior, and generic broker client API unchanged.
5. Update `BrokerSubmissionCoordinator` transitions: persist/commit new PREPARED before POST; persist accepted correlation before GET; atomically advance exactly once, schedule retry, and clear current correlation only for confirmed eligible safe-terminal results. Maintain historical outcome/audit fields separately from active correlation.
6. Wire the typed helper from summary failure/pipeline orchestration without changing failure taxonomy, YTPipe retry budget rules, direct behavior, broker policy/manifest, or Telegram notice/reply paths.
7. Add focused unit and integration coverage, execute the verification gates, then complete the required fresh-context review before requesting deployment approval.

## Test Plan

### Focused behavior

- [ ] `tests/test_broker_submission_crash_safe.py`: epoch-zero key reproduces established fixtures; epoch-positive keys are deterministic and distinct across epochs; accepted task is persisted before GET; successful safe-terminal transition increments exactly once, schedules retry, and next due attempt issues exactly one new POST/task rather than replaying the old task.
- [ ] `tests/test_broker_submission_crash_safe.py`: parameterize every exclusion—indeterminate, transport, timeout, pre-`Location`, protocol, policy/client/backend rejection, output invalid/incomplete, cancellation, unknown class, missing task ID, non-broker route, and exhausted stage budget—and assert no epoch advance/new POST.
- [ ] `tests/test_broker_submission_crash_safe.py`: simulate process reconstruction/crash before new PREPARED commit and after PREPARED/before `Location`; assert persisted state preserves Y02c quarantine semantics, zero blind POSTs/replays, and no inferred task ID. Simulate accepted task reconstruction and assert GET-only polling.
- [ ] Migration test: upgrade/backfill creates `broker_submission_epoch=0` for old rows; new rows default to zero; non-null/bounded constraints work at database level; downgrade path is tested only to the extent it is declared compatible.
- [ ] `tests/test_summary_failure_policy.py`: exact typed classes are the sole safe-terminal eligibility input and existing retry/exhaustion disposition remains intact.
- [ ] `tests/test_pipeline.py`: stage retry exhaustion never creates an epoch; confirmed safe-terminal lifecycle is integrated with due retry/status/correlation handling; direct route is unaffected.
- [ ] `tests/test_telegram_command_processing.py`: Y02c canonical sanitized Telegram/reply behavior remains green and no duplicate reply/notification is introduced.

### Commands and gates

Run from the YTPipe checkout using its existing Python environment; do not introduce package scripts or alter config.

```sh
python -m pytest tests/test_broker_submission_crash_safe.py tests/test_summary_failure_policy.py tests/test_pipeline.py tests/test_telegram_command_processing.py
python -m pytest
python -m compileall -q app
git diff --check
git status --short
```

- [ ] Run the repository's existing migration upgrade/clean-install check using its documented environment; capture applied revision and default/backfill evidence without exposing `.env` values.
- [ ] Production preflight is read-only and records both `llm-broker compat desired` and `llm-broker compat effective`; confirm the existing `batch-summary max_attempts=1`, consumer `max_acceptable_attempts=1`, and 360-second deadline remain compatible. Do not change policy or manifest to make this pass.

## Rollout and Rollback

1. With explicit operator approval, take a fresh YTPipe database backup and record restore ownership before deployment.
2. Deploy code and the additive migration; make no broker configuration, policy, manifest, route, direct-fallback, or historic-stage change. Run unit/integration and migration evidence gates.
3. After separate approval, run one controlled **normal, non-Short** video canary. Do not induce a production backend failure: the safe-terminal path is demonstrated only by the automated harness.
4. Observe for 24 hours: stage/task ratio, duplicate POST/task alerts, quarantines, terminal outcome taxonomy, retry scheduling, direct-route health, and Telegram canonical replies. Investigate any ratio indicating multiple YTPipe retries consumed against one terminal task outside epoch-zero crash-residue/quarantine cases.
5. Roll back only with approval and a compatibility assessment: revert application code first; downgrade/revert the database migration only if old-code/schema compatibility and preservation of epoch/correlation evidence are proven. Never replay, mutate, resubmit, cancel, or otherwise alter `4B4R2T4w7Kg`, `QSxghp_19TY`, earlier quarantines, or other historic failed/quarantined stages as part of rollback.

## Risks and Unknowns

- Broker terminal results do not expose the raw backend cause; eligibility must rely solely on the typed broker class/code contract, not inferred provider text.
- A new epoch creates a new broker task, so broker-internal attempt counters alone cannot describe total YTPipe work. Add or confirm operational observation of stage ID, epoch, task ID (protected), and stage/task ratio without leaking opaque IDs/keys or secrets.
- Concurrency/transaction ordering is safety-critical: review locking/atomicity so repeated workers cannot advance the same terminal result twice or POST two keys for one epoch.

## Post-Implementation Review (Required)

- [ ] `@coder-heavy` self-verifies the implementation and evidence, then invokes a fresh-context `post-implementation-reviewer` before acceptance/deployment approval.
- [ ] Supply the reviewer with the diff/change summary, focused/full test output, migration revision and upgrade/backfill evidence, production-preflight compatibility evidence, and controlled canary evidence when available.
- [ ] Review scope must explicitly cover: crash safety; epoch idempotency generation; no duplicate POST; retry/status/correlation state transitions; migration compatibility; security/no secrets or opaque key/task-ID leakage; regression against Y02c; and Telegram canonical replies.
- [ ] Resolve review blockers and rerun affected gates; this review does not authorize production actions.

## Acceptance Criteria

- [ ] After a received confirmed terminal `transient_safe` or `transient_unsent` broker failure with a known task ID and remaining YTPipe budget, the next eligible stage retry produces exactly one new broker task using a deterministic, distinct epoch-positive key.
- [ ] No epoch/new task is created for ambiguous/indeterminate failures, every listed excluded class, unknown/missing correlation, non-broker route, or exhausted stage budget; Y02c quarantine and GET-only safeguards remain intact.
- [ ] Epoch-zero rows and crash residues reproduce the prior idempotency key exactly; migration defaults/backfills zero and bounds the discriminator to stage retry capacity.
- [ ] Broker `batch-summary max_attempts=1`, consumer `max_acceptable_attempts=1`, and 360-second deadline are unchanged and `llm-broker compat desired`/`effective` remain compatible.
- [ ] Focused suites, full suite, migration check, `compileall`, diff check, controlled normal-video canary, 24-hour observation, and required fresh-context review pass without direct-route or Telegram regression.
