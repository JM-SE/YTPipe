# Y02b Agnostic Route Resolver Specification

**Status:** `approved 2026-09-05` (human spec approval = implementation gate; canary data changes still require separate operational go). **Implementation:** `accepted 2026-09-06` (human implementation acceptance after correction-scope re-review PASS; canary go still separate). Creating or approving this specification alone authorizes **no** source change, migration, policy apply, credential/grant change, live probe, deployment, restart, or service intervention. It is the 3b route-switch prerequisite owned by `Y02_broker_production_cutover.spec.md` (approved 2026-09-05), and it inherits the frozen `## Design Constraint 2026-09-05 — Agnostic Route Resolver` (no per-channel `if`, data-driven route, default `direct`, canary/rollback as data-only changes).

## Context

- YTPipe baseline `321c750` is implementation context only. Verified composition roots call `build_summarization_gateway(settings)` unconditionally and always receive `DirectSummarizationGateway`: `app/main.py:172`, `app/api/routes/polling.py:231`, `app/api/routes/polling.py:234`, `app/services/telegram_command_queue.py:487`. Inference always goes direct at `app/services/pipeline.py:723` (`self.summarization_service.summarize(...)` under `_SUMMARY_INFERENCE_LOCK`).
- The existing `recovery_target` (`direct_llama` / `none`) governs post-failure recovery only (`app/services/pipeline.py:729-736`, `:764-766`; `app/services/broker_errors.py:9`) and is NOT an inference-route selector.
- Broker construction for the manual path already exists (`app/cli/broker_probe.py` wiring via `broker_connection_config`, `BrokerTaskClient`, `load_y01_profile`) and must be reused, not duplicated.
- Frozen H03/Y01 values are unchanged: workload `batch-summary`, capability `summarize`, `max_tokens=1024`, `temperature=0.7`, `16384` output bytes, `sha256:60450bde099909b93a79deb03d07576b47b8282c0e0a4ded91aa56dd1a61615e`.

## Requirements

- [ ] Add ONE resolver module (suggested `app/services/summary_route.py`) exposing a single pure function resolving route `direct` vs `broker` from `SUMMARY_ROUTE`. No channel names, channel lists, or per-channel branches in routing code.
- [ ] Route values are exactly `direct` / `broker`. Absent OR unrecognized data resolves to `direct` (safe default, logged).
- [ ] Add ONE wiring helper used by all composition roots (`app/main.py:172`, `app/api/routes/polling.py:231`, `app/api/routes/polling.py:234`, `app/services/telegram_command_queue.py:487`) so the direct-vs-broker choice exists in exactly one place. `build_summarization_gateway` (direct builder, `app/services/direct_summarization.py:11-12`) stays untouched.
- [ ] `broker` route reuses the existing manual-path construction (`broker_connection_config`, `BrokerTaskClient.from_client`, Y01 profile) and the `BrokerResult` DTO with `content` / `finish_reason` / `usage`; `BrokerProbeService` keeps the strict Spanish oracle and `length` maps to `broker_output_incomplete`.
- [ ] `recovery_target` semantics unchanged. Broker failure behavior unchanged (fail closed, `none` target, no silent fallback to direct inference).
- [ ] Code deploy with route data absent/unset changes zero observable behavior (all traffic stays direct).
- [ ] Route data carrier is frozen: the `SUMMARY_ROUTE` environment variable loaded through `Settings` (new field, default unset). Exact values `direct` / `broker`; absent or unrecognized resolves to `direct` (logged). Enabling traffic sets `SUMMARY_ROUTE=broker`; rollback unsets it or sets `direct` — data changes only, no code deploy (a service restart to pick up the env change is allowed and is not a redeploy). Per-channel granularity is explicitly out of scope: the frozen canary is all-channels, so one global switch is the whole surface.
- [ ] The resolved route is attributed per summarization: the wiring helper logs one record per resolution with route plus video/stage identifiers only (no prompts, content, credentials, or broker topology), so Y02 one-to-one reconciliation can attribute each summary to `direct` or `broker`.
- [ ] Suggested pure signature: `resolve_summary_route(value: str | None) -> Literal["direct", "broker"]` in `app/services/summary_route.py`. When route data resolves to `broker` but broker construction/config is invalid at wiring/startup time, the root fails closed (error, no traffic) — never silently falls back to direct inference. (Broker *inference* failure behavior stays as frozen: fail closed, `none` target.)

## Non-goals

- No canary percentages, scheduling, reconciliation, cancellation, load handling, or distributed exclusivity (Y02 cutover proper).
- No change to the direct gateway, manual probe CLI, strict oracle, H03 profile, contracts, or limits (never raised silently).
- No automatic retry/fallback after ambiguity (H03/G frozen rule).

## Verification and Acceptance Criteria

- [ ] Offline tests: absent data resolves `direct`; `broker` data resolves `broker`; unrecognized value resolves `direct`; exactly one wiring point (all roots covered); direct path performs zero broker HTTP calls and touches no broker/DB/provisioner credentials.
- [ ] Full Docker pytest passes under Python 3.13; compileall/py_compile and `git diff --check` pass; generated egg-info reverted.
- [ ] Direct roots remain healthy with data unset; startup, polling, and Telegram make no broker request by default.
- [ ] `@coder-heavy` self-verification complete, followed by a deep fresh-context architecture/security/acceptance review and required corrections. No visual review required.
- [ ] Human implementation acceptance of THIS spec plus a separate operational go are recorded before any canary data change or real traffic. Enabling traffic sets route data to `broker`; rollback sets it back to `direct` (data changes only, no redeploy).
- [ ] Offline tests cover the `SUMMARY_ROUTE` carrier (unset, `direct`, `broker`, unrecognized) and assert exactly one attribution record per resolution with route plus identifiers only.

## Handoff

Planner owns this specification. The user must explicitly approve it and switch manually to `@coder-heavy` for implementation. `@coder-heavy` must self-verify, then invoke a fresh-context `post-implementation-reviewer` for the required deep architecture/security/acceptance review, correct blockers, and wait for human acceptance. No visual review is required.