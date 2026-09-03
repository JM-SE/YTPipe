# Y00 Broker Gateway Offline Specification

**Status:** `review_pass_pending_human_approval`. This is not implementation acceptance or authorization; explicit human approval is still required before implementation.

**Review record (2026-09-02):** The corrected Y00 specification received independent PASS reviews for backend architecture, security, and test/acceptance coverage, with no material blocker. Retained review conclusions: Y00 runtime composition is direct-only; exact direct behavior remains preserved; broker code is dormant and available only through explicit test injection; recovery target `none` prevents broker-triggered llama restart; idempotency remains stable per logical operation; validation is broker-only; and all tests remain offline. Y01 owns runtime selection, connectivity, canary, reconciliation, and cancellation. Residual non-blocking implementation checks are polling sleep `min(1s, remaining)` and Y01 validation of token bounds/request drift.

## Context

`app/services/summarization.py` currently owns prompt construction, chunk planning, direct llama.cpp transport, and response parsing. `PipelineService._attempt_summary_stage` invokes summarization under `_SUMMARY_INFERENCE_LOCK`; `PipelineStage` remains authoritative for retries, stage state, circuit behavior, and persistence.

Y00 creates a maintainable gateway seam while preserving current YTPipe behavior above all. Product/runtime composition remains **direct-only**. The broker implementation is dormant and constructible only through explicit dependency injection in offline tests. Y01 alone may propose activation after a separate spec, review, and approval.

## Requirements

- [ ] Preserve current direct llama.cpp behavior byte-for-byte where bytes are observable and behavior-for-behavior everywhere else.
- [ ] Keep all existing `LLAMA_CPP_*` settings, names, defaults, URL construction, and deployment behavior unchanged.
- [ ] Add no provider selector, broker endpoint, broker credential, broker environment variable, settings field, deployment configuration, canary, or rollback control in Y00.
- [ ] Runtime composition is direct-only at every composition root. Broker construction requires explicit test dependency injection and is unreachable from product/runtime code.
- [ ] Never automatically fall back between broker and direct. Y00 must not contact a broker, llama-swap, any real network/runtime service, systemd, or an external database.
- [ ] Preserve pipeline ownership of locking, stage transitions, retry/circuit policy, persistence, and recovery orchestration.
- [ ] Keep the database schema unchanged; recovery routing metadata uses existing `SyncState.state_metadata`.

## Technical Approach

### Characterize before extraction

Before moving logic, freeze the current direct contract with tests covering exact prompts and payloads, timeout and URL, response parsing, whitespace, exception text/details, and request sequencing. Extraction is mechanical: constants, comparison boundaries, ordering, trimming, and exceptions must be copied rather than redesigned. If characterization contradicts this spec's description of current behavior, stop and return the discrepancy to the planner; do not silently choose a new behavior.

### Focused ownership

Adapt file names to established project convention while retaining these boundaries:

| Planned unit | Single responsibility |
| --- | --- |
| Gateway contract module | `SummarizationGateway` protocol plus frozen/immutable context and logical-operation DTOs. Result is `str`. |
| Pure prompt/chunk planner | Existing prompt text, direct split/chunk plan, operation order, and payload-independent operation descriptors. No HTTP, persistence, retry, clock, or validation. |
| Direct gateway/adapter | Existing llama.cpp HTTP transport, OpenAI-compatible payload, parsing, trimming, and exact public `SummarizationRequestError` compatibility. |
| Dormant broker client/gateway | Generic B00 v0.1 submit/poll state machine using only injected transport, monotonic clock, sleeper, and constructor timeout. |
| Broker output validator | Broker-only normalization/shape oracle. No direct-path invocation. |
| Sanitized broker error mapping | Static allowlisted local classifications/messages with no remote interpolation. |
| Composition helper | Constructs the direct implementation only. It has no provider branch or broker inputs in Y00. |
| Pipeline integration | Receives the protocol and passes durable stage context; continues to own lock, stage/retry/circuit/persistence/recovery behavior. |

Do not combine contract, planning, both transports, validation, composition, and pipeline policy in one module. Keep an existing public façade only where required for import/API compatibility.

### Direct compatibility contract

The direct implementation must preserve all characterized behavior, including:

- the `30_000` direct threshold and `24_000` chunk size;
- behavior at `30_000` and `30_001`, no-space splitting, and every existing split boundary;
- exact system/user prompts for partial and final summaries;
- the exact OpenAI-compatible JSON field set and ordering where observable, including the hard-coded model, `temperature`, `max_tokens`, and `stop`;
- URL construction, the current configured `300` second timeout behavior, and transport invocation;
- response shape parsing, whitespace `.strip()` behavior, and non-empty-after-strip acceptance;
- sequential partial requests followed by exactly one aggregate-final request, preserving request count and order; and
- every existing direct exception type, public message, and provider detail.

No new output oracle applies to direct mode. Direct output is accepted if and only if current code accepts it: non-empty after existing stripping/parsing. Broker strict validation is a separate boundary and may not alter the direct façade, payload, errors, or persistence behavior.

### Gateway context and logical operations

The immutable context contains the durable `PipelineStage.id` needed for identity and no transcript-derived identifier. The pure plan emits one logical operation for a direct-sized request or N partial operations followed by one aggregate operation:

- contract version: `ytpipe-summary-v1`;
- operation kind: `direct-final`, `partial`, or `aggregate-final`;
- zero-based ordinal: `0` for `direct-final`; chunk index for `partial`; and `N` for `aggregate-final` after N partials.

For broker POSTs, derive `Idempotency-Key` as lowercase hexadecimal SHA-256 over this canonical byte encoding, in field order: contract version, decimal `PipelineStage.id`, operation kind, decimal ordinal. Encode each UTF-8 field as `uint32` unsigned big-endian byte length followed by its bytes, concatenate the four frames, then hash. The resulting 64 printable ASCII characters satisfy the API's 8–200 character rule.

The key contains no transcript, prompt, video/user identity, model/provider/backend/endpoint, credential, task ID, random value, or `PipelineStage.attempt_count`. The same logical operation reuses the same key across submit replays, polling, process restarts, and ambiguous timeouts. Different operation kinds, ordinals, or stage IDs produce different keys. No internal POST retry is added; a later durable pipeline attempt may replay POST with the same key.

Y00 does not solve generation rotation after a terminal broker failure. Without a durable broker-operation ledger/generation, broker activation is blocked. Y01 must specify and prove terminal-failure reconciliation and new-generation policy before activation.

### Dormant Broker API v0.1 contract

Normative reference: llm-broker `openapi.yaml`, API version `0.1.0`, and its API documentation. The injected client submits exactly:

- `POST /v1/tasks`;
- headers: `Authorization: Bearer <injected credential>`, `Idempotency-Key`, `Content-Type: application/json`, and `Prefer: wait=30`;
- body workload `batch-summary`, capability `summarize`, and `messages` containing the existing planned system/user prompt pair in order;
- generation `{ "max_tokens": <current operation value>, "temperature": 0.7 }`; include `stop` only when the existing value is valid and non-empty;
- response request `{ "kind": "text" }`;
- no provider, model, backend, application/domain identity, video/user/transcript/task metadata, or trace metadata.

Handle a valid `200` terminal `TaskResult`. For `201`/`202`, capture and validate `Location`/task identity, then poll `GET /v1/tasks/{id}/result`. Accept only a relative Location, or same-origin Location relative to the injected base URL, whose normalized path identifies exactly one task; reject traversal, query/fragment identity, origin changes, missing/mismatched IDs, or any shape not admitted by v0.1. Task states are `pending`, `succeeded`, `failed`, `cancelled`, and `expired`; only a well-formed succeeded text result can reach validation. Unknown status, unknown/malformed shape, malformed JSON, and non-conforming body fail closed.

Timing is deterministic and injected:

- constructor timeout is inherited from the current direct timeout argument; introduce no Settings field;
- effective overall deadline is `min(injected_timeout, 300s)` from the injected monotonic start;
- `Prefer: wait=30` is the initial server wait request, not a local sleep;
- after an accepted non-terminal response, sleep at most `1s` and never beyond remaining time, then poll;
- maximum GET polls is `ceil(effective_deadline_seconds / 1s)` and every request also receives no more than the remaining deadline;
- timeout before a known task identity and any ambiguous submit outcome raises a sanitized retryable failure; the next pipeline attempt replays the same POST key;
- timeout after a validated task identity leaves the stage retryable and does not cancel, resubmit within the same call, or switch providers.

Y00 sends no cancel request. Disconnect/timeout must not cancel ambiguous work. Cancellation semantics are deferred to Y01.

### Broker-only output oracle

Validation runs only on successful broker text. It returns the normalized broker string or a sanitized broker validation failure:

1. Convert `CRLF` and bare `CR` to `LF`.
2. Apply Unicode `strip()` to the whole value; remove trailing spaces/tabs from each line. Do not collapse internal spaces or blank lines.
3. Reject empty output and case-insensitive occurrences of `<think` or `</think>` anywhere.
4. Reject any line whose optional leading whitespace is followed by a Markdown heading marker (`#`), a three-backtick or `~~~` fence, or a numbered-list prefix (`1. ` or `1) ` pattern).
5. Require heading lines exactly `RESUMEN`, `PUNTOS CLAVE`, and `CONCLUSIÓN`, in that order, each exactly once and alone on its line. Leading whitespace, punctuation, or case variation is invalid.
6. Treat any other all-uppercase, letters/spaces-only line as an extra top-level heading and reject it.
7. Require at least one non-blank line in `RESUMEN` and `CONCLUSIÓN`.
8. In `PUNTOS CLAVE`, require exactly 4–7 non-blank lines; every one must begin with `•` in column zero and contain non-whitespace text after the bullet.

Exact valid fixture:

```text
RESUMEN
Resumen fiel.

PUNTOS CLAVE
• Punto uno
• Punto dos
• Punto tres
• Punto cuatro

CONCLUSIÓN
Conclusión fiel.
```

Invalid fixture families must pin: empty/whitespace; either think-tag form and mixed case; missing, duplicate, reordered, indented, punctuated, or case-changed headings; empty section; 3 or 8 bullets; indented/empty/non-bullet point; Markdown heading/fence; numbered list; extra uppercase top-level heading. Add one normalization fixture proving CRLF, outer whitespace, and trailing spaces normalize exactly while internal blank lines/spaces are retained.

Spanish/source fidelity and non-invention remain prompt, characterization, and later evaluation expectations; they are not claimed as mechanically provable validator assertions.

### Failure, recovery, and privacy contract

Introduce provider-neutral internal gateway failure classification/metadata without changing the direct public exception contract. Every failure carries an internal recovery target:

- direct failures default to `direct_llama` and retain exact current `SummarizationRequestError` messages/details and current circuit/`LlamaRecoveryService` behavior;
- broker transport/protocol/task/timeout/validation failures use `none` and one static allowlisted local error code/message;
- no broker mapping may interpolate response/problem body, remote detail/text, URL, headers, credential, idempotency key, transcript, task ID, or other remote data into exceptions, logs, `SyncState`, the database, or Telegram alerts.

Known HTTP/RFC7807 problem codes and each local error class map through a static table; unknown HTTP/problem codes and malformed responses map to a generic local broker protocol error. Tests inspect exception text, logs, persisted metadata, and alert text for forbidden values.

Persist recovery routing in existing `SyncState.state_metadata` under one namespaced field chosen consistently by the implementation (for example `summary_failure.recovery_target`), with no migration:

1. On a summary failure, set `direct_llama` or `none` before/with the existing paused-circuit persistence.
2. Retain it while paused and across restart/reload.
3. Missing, malformed, or legacy metadata resolves to `direct_llama` for compatibility.
4. Polling and Telegram recovery decisions read the resolved target. `none` must skip `LlamaRecoveryService`, subprocess, and systemd while retaining the existing paused/retryable stage and user-visible sanitized failure flow.
5. Clear the namespaced field after successful summary persistence, successful recovery, or circuit closure/reset. Preserve unrelated `state_metadata` keys.

The pipeline still pauses/persists its circuit and leaves the summary stage retryable on a broker failure. This routing addition must not otherwise change direct failure, retry, circuit, or recovery behavior.

### Runtime composition manifest

Characterize and test every current root:

- startup `app/main.py`;
- `app/api/routes/polling.py::_build_polling_service`;
- `YouTubePollingService` construction;
- reconciliation/drain construction paths; and
- `TelegramCommandQueueService._build_pipeline`.

All receive the same direct implementation in Y00, with the existing settings/defaults. There is no runtime path to broker construction. Address or remove a redundant `YouTubePollingService.summarization_service` only if characterization proves it unused and regression tests prove no behavior change; otherwise preserve it.

## Implementation Steps

1. Add direct characterization tests before extraction, including exact bytes/fields, boundaries, splitting, whitespace, errors, and partial/final sequencing.
2. Add immutable gateway/context/operation contracts and pure prompt/chunk planning; prove byte-equivalence against characterized direct behavior.
3. Isolate the direct adapter while preserving its public façade, transport, payload, parsing, and errors exactly.
4. Add provider-neutral recovery metadata and persistence lifecycle without changing direct recovery behavior.
5. Add the broker-only validator and static sanitized error mapper.
6. Add the dormant broker client/gateway with injected in-memory transport, clock, sleeper, timeout, and credential; expose no runtime constructor path.
7. Inject the protocol through the pipeline and all composition roots while keeping composition direct-only and pipeline ownership unchanged.
8. Complete offline verification, coder self-verification, fresh-context post-implementation review, and explicit human acceptance.

Planner owns this specification. After approval, `@coder-heavy` owns implementation, integration, and verification.

## Offline Test Gates

- [ ] **Pre-extraction characterization:** exact direct payload and field ordering where observable, URL, timeout, hard-coded model/generation/stop, response parsing, direct errors/provider detail, `30_000`/`30_001`, no-space split, whitespace, prompt bytes, and partial/final count/order.
- [ ] **Pure units:** planner byte equivalence; frozen DTO mutation rejection; key canonical-encoding golden vector with a fixed expected digest; differences by stage/kind/ordinal; replay, restart, ambiguity, and chunk ordinal stability; proof `attempt_count` is excluded.
- [ ] **Validator/mapping units:** all exact valid/invalid/normalization fixtures; known and unknown sanitized mappings; forbidden-data non-reflection.
- [ ] **Broker transport:** injected `httpx.MockTransport` or equivalent in-memory transport, never a listener. Cover 200/201/202, Location validation, pending-to-success, every terminal state, known/unknown RFC7807, malformed JSON/body, remaining-time propagation, deadline/poll bound, ambiguous timeout replay with the same key, no cancel, exact envelope/headers, and absence of forbidden semantics.
- [ ] **Pipeline SQLite integration:** a blocking fake proves `_SUMMARY_INFERENCE_LOCK` spans the complete gateway call and concurrent attempts do not overlap; successful summary persists; invalid broker output does not; retry/circuit behavior is unchanged; broker failure persists paused/retryable with recovery `none`, reloads as `none`, and makes zero restart calls; legacy/missing metadata and direct failure retain current restart behavior.
- [ ] **Composition manifest:** tests for every named root prove Y00 builds direct only, retains existing settings/defaults, and issues zero broker requests.
- [ ] **Suite deny guards:** fail tests on outbound sockets/network, real broker/llama endpoints, subprocess/systemd, or real sleep; broker tests require injected transport/clock/sleeper. Use only temporary local SQLite—no migration, external DB, Docker, or runtime service.
- [ ] **Static audit:** direct llama.cpp HTTP exists only in the direct adapter; dormant broker transport is reachable only by explicit injection/tests; no provider branch/config/deploy change, broker semantic leakage, automatic fallback, or cancel path exists.
- [ ] Run targeted and full project pytest suites, plus project-defined lint and typecheck; inspect formatting and git diff. No failing existing test may be waived as unrelated without review evidence.

## Acceptance Criteria

- [ ] Runtime/product composition remains direct-only and all current direct behavior and public failures are unchanged.
- [ ] Broker code is dormant, generic, constructible only by explicit offline-test injection, and incapable of fallback, runtime contact, cancel, or llama restart.
- [ ] Idempotency is deterministic per durable logical operation and stable across retry/restart/ambiguity; `attempt_count` never rotates it.
- [ ] Broker output alone is subject to the deterministic strict oracle; direct output acceptance is unchanged.
- [ ] Recovery target persists/reloads/clears as specified; broker `none` never invokes recovery/systemd, while direct and legacy behavior remain compatible.
- [ ] No secret, transcript, identity, task/key, URL, remote detail, or response content leaks through broker errors, logs, state, DB, or alerts.
- [ ] All offline gates pass with no schema, runtime settings, deployment, external contact, or multi-responsibility mega-module.

MVP blockers are any direct drift, runtime selection/configuration, external contact, automatic fallback, possible broker-triggered llama restart, privacy leakage, nondeterministic idempotency, or missing offline gate.

Y01-only, non-blocking for dormant Y00 but blocking before activation: actual broker connectivity, settings/provider selection, canary/manual rollback, durable terminal-failure generation/reconciliation ledger, cancellation semantics, and load/distributed exclusivity. Each requires a separate specification and approval.

Post-implementation review is required: `@coder-heavy` self-verifies first; then a fresh-context post-implementation reviewer checks direct regression, privacy, recovery routing, idempotency, offline isolation, composition, and scope. Explicit human acceptance is required. Until then this phase remains `review_pass_pending_human_approval`, not implementation-accepted or authorized.
