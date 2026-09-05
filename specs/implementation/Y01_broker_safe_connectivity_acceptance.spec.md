# Y01 Broker Safe Connectivity and Controlled Acceptance Specification

**Status:** `implemented_offline_verified_acceptance_blocked_on_H03`. Y01
implementation and offline review exist, and manual synthetic connectivity
succeeded. Y01 is **not accepted**: final real-URL acceptance is blocked on
accepted/implemented broker H03 and separate operational approval. This remains
a narrow connectivity gate, not product broker activation. It depends on
accepted Y00 commit `ff05558` and does not redefine Y00. YTPipe baseline
`8ae6aa5` is context, not acceptance evidence.

## Context

Y01 proves broker connectivity, authentication, policy/protocol compliance, and
strict output validation without enabling broker production traffic. Existing
implementation/offline review and the successful synthetic are preserved
evidence, but the real-URL gate cannot complete safely until H03 supplies
declarative compatibility and an explicit output-byte bound. Product summary
routes remain direct llama-server through `LLAMA_CPP_BASE_URL`.

This is not a broker rollout, provider selection, canary percentage, normal summary routing, reconciliation, generation policy, cancellation, load testing, or distributed-exclusivity work.

## Requirements

- [x] Provide a standalone manual/TTY-only CLI with no admin HTTP endpoint and
  no path from startup, normal FastAPI routes, polling/drain/reconciliation,
  Telegram, `PipelineService`, execution locks, `LlamaRecoveryService`, direct
  gateway composition, subprocesses, or systemd.
- [x] The synthetic probe uses only fixed harmless Spanish fixture content and
  prompt, is independent of users/videos/transcripts, proves authenticated
  submit/poll, and accepts only strict oracle-valid output. Manual synthetic
  connectivity has succeeded.
- [ ] The final controlled URL probe runs exactly once, only after desired and
  effective H03 compatibility PASS, broker ready/worker idle, no unresolved
  indeterminate task, synthetic PASS, interactive URL supply/confirmation, and
  explicit consent. This is the privacy boundary for transcript-derived content.
- [x] URL probing is isolated from polling, Telegram, startup, reconciliation,
  notifications, metadata lookup, persistence, and the direct llama gateway.
- [x] URL input remains stdin/TTY-only and absent from spec/config/arguments/
  source/logs/fixtures/database/commits; canonicalize with
  `parse_youtube_video_url`, show the canonical URL, and require the consent
  phrase before fetch or submit.
- [ ] Do not print content or expose/use `--show-summary` in the final H03-backed
  acceptance path. Retained evidence contains sanitized status/categories only.
- [ ] Enforce one invocation and broker workload `max_attempts=1`. Never retry,
  replay/resubmit POST, rotate to a new idempotency key, cancel/delete, fall back,
  restart/recover, schedule, or run background work after ambiguity. Resolve any
  indeterminate task through the supported operator flow before another
  inference.

### Probe configuration

- [x] Preserve the optional disabled probe flags, broker URL/token validation,
  strict origin rules, redirects disabled, `trust_env=False`, TLS verification,
  bounded HTTPX timeouts, and startup independence already implemented.
- [ ] Freeze the manual probe overall deadline at 360 seconds and raise only its
  broker-timeout upper bound to 360. Do not change direct llama timeout or normal
  runtime behavior.
- [ ] Require a generic backend response-start/header budget of 60 seconds in the
  manifest. Keep it independent from the 300-second workload timeout and
  360-second consumer deadline; do not derive or collapse these budgets.
- [ ] Preserve the existing 30,000-character transcript rejection before submit;
  never truncate. Declare manifest `max_request_content_bytes=131072` as
  conservative combined system+user capacity, not a transcript byte limit. It
  remains below broker global 262,144 combined-message bytes.
- [x] Add no provider selector, `SUMMARIZATION_MODE`, canary percentage,
  allowlist, runtime fallback, `LLAMA_CPP_*` change, startup healthcheck, or
  normal-composition broker construction. Keep tokens out of systemd, command
  lines, logs, examples, and tracked files.

### Protocol, privacy, and idempotency

- [ ] Adopt broker OpenAPI `0.1.1` and exact terminal envelopes: pending
  `{status}`, succeeded `{status,result}`, failed `{status,error}`, and
  cancelled/expired `{status}`. Accept valid `internal`; failed always requires
  error.
- [ ] Add the YTPipe-owned tracked manifest at
  `app/contracts/broker/y01.consumer-compat.json` and package it as data if
  required. It is the single source for API `>=0.1.1 <0.2.0`, workload
  `batch-summary`, capability `summarize`, response `text`, `max_tokens=1024`,
  `temperature=0.7`, required output bytes `16384`, request-content bytes
  `131072`, minimum backend response-start/header timeout `60`, workload timeout
  `300`, maximum attempts `1`, and consumer deadline `360`. Deployment-specific
  broker policy remains private/operator-owned; acceptance config sets explicit
  `max_output_bytes=16384`.
- [ ] `app/services/broker_profile.py` strictly loads and validates that manifest
  and derives request/profile fields. Do not duplicate manifest literals in
  Python. The same frozen profile—including generation, output requirement, and
  text response—applies to both synthetic and URL probes; keep the synthetic
  fixture harmless.
- [ ] `BrokerTaskClient` remains transport/protocol only and accepts the profile/
  request contract rather than hardcoding workload, capability, or temperature.
  Its result DTO retains `content`, `finish_reason`, and `usage` and performs no
  application validation.
- [ ] `BrokerProbeService` owns the strict Spanish oracle, accepts only
  `finish_reason=stop`, and maps `length` to sanitized
  `broker_output_incomplete`, not protocol corruption.
- [ ] The operator runs `llm-broker compat desired --manifest <Y01-path>` and
  `llm-broker compat effective --manifest <Y01-path> --client <name>` as separate
  broker CLI commands before starting the YTPipe probe CLI. The YTPipe CLI MUST
  NOT invoke the broker executable, run subprocesses, or access PostgreSQL or
  provisioner credentials.
- [ ] The YTPipe CLI validates/uses its own tracked non-secret manifest/profile
  and runs only after operator preflight. It does not claim to independently
  prove effective policy. Desired/effective output and YTPipe test evidence
  correlate the normalized semantic manifest digest as `sha256:<hex>` without
  exposing manifest content, database details, or backend identity/value.
- [x] Production CLI uses configured transport while tests inject transport,
  clock, and sleeper; HTTPX debug logging is disabled/avoided.
- [x] Present static sanitized categories only. Never render, log, or persist URL,
  token/header, task ID, key, prompt/content, transcript, canonical URL, summary,
  response body, remote details, or exception details.
- [x] Preserve idempotency namespace `ytpipe-broker-probe-v1` and its
  length-framed SHA-256 identity. It excludes URL/video/user/transcript/model/
  token/task/attempt data and cannot rotate after ambiguity.

## Technical Approach

Maintain focused units; do not create a god module.

| Module | Responsibility |
| --- | --- |
| `app/contracts/broker/y01.consumer-compat.json` | Tracked, packaged, non-secret Y01 manifest; single source for workload, capability, generation, response, and requirement values. |
| `app/services/broker_profile.py` | Strict manifest loader/validator and request/profile derivation. It duplicates no manifest literals and owns no transport, settings, TTY, or app data. |
| `app/services/broker_connection_config.py` | CLI-only probe-settings validation and HTTPX client construction/closure; manual deadline may reach 360 seconds without changing direct/runtime timeouts. |
| extracted low-level `BrokerTaskClient` from `broker_gateway.py` | OpenAPI 0.1.1 transport/protocol only: submit, `Location`, polling, exact terminals, and sanitized mapping for a supplied profile/request and key. It preserves content/finish_reason/usage and owns no oracle. |
| `app/services/broker_probe.py` | `BrokerProbeService`: fixed synthetic input, URL-probe orchestration, 30,000-character rejection, strict Spanish oracle, `finish_reason=stop`, and sanitized `length` handling. No settings, TTY, DB, pipeline, or logging. |
| `app/cli/broker_probe.py` (or established CLI equivalent) | Runs after separate operator compatibility preflight; validates/uses the tracked profile, performs TTY/stdin URL confirmation/consent, and emits only sanitized output plus digest correlation. It never invokes broker CLI/subprocesses, accesses DB credentials, accepts a URL argument, or displays summary content. |
| app-neutral fixture runner | Verifies bundle version/SHA-256 and executes a vendored byte-identical snapshot or explicitly mounted broker canonical bundle in tests only; application runtime never reads another repository. |
| optional shared pure idempotency helper | Serve distinct Y00/probe namespaces only when it cleanly does so; never force `SummaryGatewayContext` or `PipelineStage.id` to represent probes. |

Use the existing URL parser and transcript service only behind a narrow acceptance
adapter; do not persist models. The CLI remains the only composition root for
tracked-profile validation and probe client construction; broker compatibility
checks remain separate operator-run broker commands. All normal roots continue
constructing direct-only behavior, including when probe settings are present.

## Implementation Steps

1. Preserve the existing implementation, offline review, isolation evidence, and
   successful synthetic evidence; do not reinterpret them as acceptance.
2. After H03 is approved and implemented, add the tracked YTPipe-owned manifest
   and derive the immutable request/profile from it without duplicated Python
   literals.
3. Update the task-client DTO/parser to OpenAPI 0.1.1 exact terminals while
   retaining content, finish reason, and usage; move all oracle policy to the
   probe service.
4. Freeze `stop`/`length` handling, independent 60/300/360-second budgets,
   one-attempt ambiguity behavior, separate operator compatibility preflight,
   safe digest correlation, and versioned/digested generic fixtures.
5. Re-run complete offline verification and deep fresh-context review. Correct
   blockers and obtain human implementation acceptance before operations.
6. With separate operational approval, the operator runs broker desired/effective
   commands against the tracked manifest and confirms their matching digest,
   then confirms ready/idle/no indeterminate task before starting the separate
   YTPipe CLI. Run synthetic, then exactly one stdin/TTY consented URL probe
   without displaying content. Record sanitized digest-correlated evidence,
   clean temporary credentials/stack, and stop; do not begin Y02.

## Verification and Acceptance Criteria

- [x] Existing implementation/offline review proves direct-only composition,
  conditional probe configuration, isolated manual CLI, injected transport/
  clock/sleeper, strict URL/consent behavior, no persistence/fallback/recovery,
  and non-disclosing output. Preserve that evidence.
- [x] Manual synthetic connectivity succeeded with sanitized evidence. This does
  not satisfy the H03 compatibility, output-byte, real-URL, or acceptance gates.
- [ ] `app/services/broker_profile.py` loads/validates the tracked manifest and
  derives request/profile values with no duplicated literals. Synthetic and URL
  probes use the same frozen values; desired/effective compatibility pass against
  that file with explicit broker `max_output_bytes=16384`.
- [ ] Compatibility vectors prove default OpenAI-compatible response-header
  timeout 15 fails the manifest's 60-second requirement with
  `backend_response_start_timeout_insufficient`, configured 60 passes, and no
  check contacts a backend/network. Workload timeout remains 300 and consumer
  deadline remains 360 independently.
- [ ] The vendored or explicitly mounted broker canonical fixture bundle passes
  unchanged in YTPipe; tests verify bundle version, SHA-256, and vector equality,
  while normal runtime has no cross-repository dependency. Exact 0.1.1 terminal
  parsing accepts status-only cancelled/expired and valid `internal`, while
  failed requires error; result DTO preserves content/finish_reason/usage.
- [ ] Tests prove `stop` is the only accepted finish reason and `length` maps to
  `broker_output_incomplete`; no application oracle remains in
  `BrokerTaskClient`.
- [ ] Transcript tests prove the unchanged 30,000-character cap and manifest
  131,072-byte capacity without truncation. Manual broker timeout accepts 360
  seconds without changing direct llama or normal runtime timeouts.
- [ ] Ambiguity tests prove `max_attempts=1` and zero retry, replay/resubmit, new
  key, fallback, cancel, or further inference until operator resolution.
- [ ] Isolation tests prove zero database/model/stage/`SyncState`/notification/
  Telegram/polling/pipeline/lock/llama-recovery/subprocess/systemd/direct-llama
  calls. Startup, polling, and Telegram issue no broker request or compatibility
  check.
- [ ] CLI tests prove the broker executable is never invoked, no subprocess is
  run, and PostgreSQL/provisioner credentials are neither read nor required. The
  CLI validates its own manifest/profile after documented operator preflight and
  never reports that it independently proved effective policy.
- [ ] Secret-sentinel checks across logs, caplog, stdout/stderr, exceptions, and
  fixtures prove no URL, credential, key/task ID, prompt/content, transcript,
  summary, remote detail, database URL, model/backend, or provider disclosure.
- [ ] Full Docker pytest passes under Python 3.13, plus compileall/py_compile and
  repository-required static checks; `git diff --check` passes and generated
  egg-info is reverted.
- [ ] `@coder-heavy` self-verifies, then invokes a deep fresh-context
  architecture/security/acceptance post-implementation reviewer and corrects all
  blockers. Human implementation acceptance follows; no visual review is needed.
- [ ] After separate operational approval: separate broker desired PASS and
  effective PASS outputs plus YTPipe tests carry the same `sha256:<hex>` semantic
  manifest digest; then broker ready/worker idle/no unresolved indeterminate
  task, synthetic PASS, and exactly one previously or explicitly consented URL
  PASS without `--show-summary`. Evidence exposes no manifest content, DB or
  backend details. Direct YTPipe remains healthy, temporary stack/credentials
  are cleaned, and no Y02 or production broker route is activated.
- [ ] Rollback disables probe flags/stops probe use and revokes the scoped
  credential; restart only with separate approval when reload requires it. Do
  not cancel or repeat ambiguous broker work and never auto-delete data/volumes.
- [ ] Scope remains exact: connectivity acceptance only, no normal broker route,
  product activation, production canary/selection, persistence, or direct drift.

## Deferred Work

Y02 remains `future` / `deferred` / `unapproved`. Only a new specification and
human approval after Y01 acceptance may authorize production routing selection/
canary, durable terminal reconciliation/generation policy, cancellation, load
behavior, or distributed exclusivity.

## Handoff

Planner owns this specification. The user must switch manually to `@coder-heavy`
only after explicit H03 approval. `@coder-heavy` self-verifies and then invokes a
fresh-context `post-implementation-reviewer`; Y01 remains unaccepted until
corrections, human approval, and the controlled final evidence above. No visual
review is required.
