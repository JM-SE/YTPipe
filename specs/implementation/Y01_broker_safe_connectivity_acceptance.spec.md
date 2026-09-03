# Y01 Broker Safe Connectivity and Controlled Acceptance Specification

**Status:** `draft_pending_human_approval`. This specification grants no execution authorization. It supersedes the former broad Y01 activation intent with a deliberately narrower safety gate. It depends on implemented and accepted Y00 commit `ff05558` and does not redefine Y00.

## Context

Y01 proves broker connectivity, authentication, policy/protocol compliance, and strict output validation without enabling broker production traffic. Product summary routes remain direct llama-server through `LLAMA_CPP_BASE_URL`.

This is not a broker rollout, provider selection, canary percentage, normal summary routing, reconciliation, generation policy, cancellation, load testing, or distributed-exclusivity work.

## Requirements

- [ ] Provide a standalone, manual-only CLI; do not add an admin HTTP endpoint.
- [ ] Make the CLI unreachable from startup, normal FastAPI routes, polling/drain/reconciliation, Telegram commands, `PipelineService`, execution locks, `LlamaRecoveryService`, the direct gateway, subprocesses, and systemd.
- [ ] The mandatory synthetic probe shall submit only fixed, harmless Spanish fixture content and a fixed prompt. It is independent of users, videos, and transcripts, proves authenticated B00 submit/poll, and accepts only strict oracle-valid output.
- [ ] The optional controlled YouTube acceptance probe shall run only after the operator interactively supplies a URL and confirms it. It may fetch that transcript and submit it once to the broker, which is an explicit operator consent/privacy boundary because transcript-derived content leaves YTPipe.
- [ ] The URL acceptance probe shall not become polling, Telegram, startup, or reconciliation traffic; notify; call metadata lookup; persist any `Video`, `PipelineStage`, `SyncState`, `NotificationDelivery`, DB row, file, or task result; or make a direct llama gateway call.
- [ ] URL input is an execution precondition, not a design blocker. Never place it in this spec, config, command arguments, source, logs, fixtures, DB, or a commit. Read it through interactive stdin/TTY only, parse/canonicalize with existing `parse_youtube_video_url`, display the canonical URL for confirmation, and require an explicit consent phrase before transcript fetch or submission.
- [ ] Do not print content by default. `--show-summary` is optional, terminal-only, TTY-only, and must warn that the result can enter terminal scrollback. It shall never reach application logs or files.
- [ ] Do not automatically retry, replay POST, cancel/delete, fall back to llama-server, recover/restart via systemd/subprocess, schedule work, or run a background worker. An operator may reuse a supplied probe ID after an ambiguous failure; Y01 provides no local durable exactly-once claim.

### Probe configuration

- [ ] Add only optional probe settings: `BROKER_PROBES_ENABLED=false`, `BROKER_ACCEPTANCE_PROBE_ENABLED=false`, `BROKER_BASE_URL=` (no default), `BROKER_BEARER_TOKEN=` (no default), `BROKER_TIMEOUT_SECONDS` (bounded `[1,300]`, defaulting to the existing llama timeout only when the CLI executes), and bounded documented `BROKER_PROBE_MAX_TRANSCRIPT_CHARACTERS`.
- [ ] Reject transcripts above the cap before submit; do not silently truncate or partially disclose content.
- [ ] Do not add a provider selector, `SUMMARIZATION_MODE`, canary percentage, allowlist, runtime fallback, changes to `LLAMA_CPP_*`, startup healthcheck, or normal-composition broker construction.
- [ ] `Settings.validate_runtime_config()` must not validate broker-probe settings or impair startup while flags are false. A CLI-only parser/factory shall require URL and token together; reject blank/placeholder/CR-LF tokens; validate an absolute base URL without userinfo, query, or fragment; enforce exact and testable origin restrictions; and require HTTPS outside a documented explicit loopback-development HTTP exception.
- [ ] The factory shall use `httpx.Client` with redirects disabled, `trust_env=False`, TLS verification, and bounded connect/read/write/pool timeouts. Do not put tokens in systemd, command lines, logs, or documentation examples; `.env` remains untracked and owner-only during operation.

### Protocol, privacy, and idempotency

- [ ] Reconcile current Y00 B00 v0.1/llm-broker OpenAPI before implementation; it remains normative. Submit and poll use only its bounded existing behavior.
- [ ] Production CLI operation uses the configured client; tests inject transport, clock, and sleeper. Disable/avoid HTTPX debug logging for invocation.
- [ ] Present static, sanitized categories/messages only. Never render, log, or persist base URL, token/auth header, task ID, idempotency key, prompt, transcript, raw/canonical URL, summary (except explicit TTY `--show-summary`), response body, remote details, or exception details.
- [ ] Use probe idempotency namespace `ytpipe-broker-probe-v1`. Length-framed SHA-256 input includes contract version, probe kind (`synthetic` or `youtube`), opaque caller/runtime probe ID, operation kind, and ordinal; excludes URL, video, user, transcript, model, token, task ID, and attempt.

## Technical Approach

Maintain focused units; do not create a god module.

| Module | Responsibility |
| --- | --- |
| `app/services/broker_connection_config.py` | CLI-only probe-settings validation and HTTPX client construction/closure; no prompts, transcripts, or TTY. |
| extracted low-level `BrokerTaskClient` from `broker_gateway.py` | Exactly B00 submit, `Location` validation, result polling, and static error mapping for supplied `BrokerOperation` and idempotency key. Keep `BrokerSummarizationGateway` as the Y00 `PipelineStage` adapter and preserve direct behavior. |
| `app/services/broker_probe.py` | `BrokerProbeService`: fixed synthetic request, acceptance input orchestration, private-content result DTO with status/category only, character-cap enforcement, fresh probe-ID validation/generation, and separate canonical idempotency encoding. No settings parsing, TTY, DB, pipeline, or logging. |
| `app/cli/broker_probe.py` (or established CLI equivalent) | TTY/stdin interaction, canonical-URL confirmation, consent phrase, sanitized stdout/stderr and exit codes, and dependency injection. It never accepts a URL argument. |
| optional shared pure idempotency helper | Serve distinct Y00/probe namespaces only when it cleanly does so; never force `SummaryGatewayContext` or `PipelineStage.id` to represent probes. |

Use existing URL parser and transcript service only behind a narrow acceptance adapter; do not persist models. The CLI must be the only composition root for probe configuration/client construction. All normal roots must continue constructing direct-only behavior, including when probe settings are present.

## Implementation Steps

1. Reconcile the broker OpenAPI and characterize/extract the task-client seam without changing direct-path requests or behavior.
2. Add the probe configuration/factory with conditional validation; prove normal app startup is unchanged without broker configuration.
3. Add the isolated probe domain, idempotency contract, fixed Spanish synthetic operation, and strict output oracle.
4. Add the manual CLI and URL acceptance adapter with stdin-only URL, canonical confirmation, explicit consent, transcript cap, no persistence, and opt-in TTY-only summary display.
5. Complete offline/mock verification and keep deployment disabled. An operator then configures a scoped credential outside the repository and runs synthetic; only after providing a public URL and consent may they run the one-off URL experiment. Record only a sanitized outcome outside the repository and do not proceed to real traffic.

## Verification and Acceptance Criteria

- [ ] All current direct composition roots construct only direct behavior even with probe settings configured; no normal activity invokes the broker.
- [ ] Missing, partial, or invalid probe configuration creates zero network traffic and does not block direct startup. The enabled CLI rejects invalid URL, token, timeout, and origin before a request.
- [ ] Mock `httpx.MockTransport` plus injected clock/sleeper prove exact B00 requests; accepted 200/201/202 handling; polling/deadline/`Location` validation; disabled redirects; no retry, cancel, fallback, or ambient proxy (`trust_env=False`).
- [ ] Synthetic input is the exact fixed fixture and output passes its strict oracle only.
- [ ] URL tests prove stdin-only input, URL-parser matrix, canonical-confirmation refusal, consent refusal, one URL/one probe, unavailable or oversized transcript rejection before submit, no metadata lookup, and TTY-only opt-in output display.
- [ ] Isolation tests/mocks prove zero DB session/SQL mutation/models/stages/`SyncState`/notifications/Telegram/polling/pipeline/execution lock/llama recovery/subprocess/systemd/direct-llama calls. Snapshot temporary SQLite state before/after if the harness initializes it.
- [ ] Privacy verification with caplog, stdout/stderr, and static audit proves prohibited remote/content values are absent; the only permitted summary display is explicit TTY `--show-summary`.
- [ ] Run full project pytest and project-defined compile, type, lint, formatting, and `git diff --check` commands. Docker, runtime, and live-network tests are not automated gates.
- [ ] Controlled operator checklist: protect a scoped token outside the repository; verify direct-service health; successfully run synthetic; only then provide a URL and consent; verify result status without persistence; confirm direct service still works; then disable flags and revoke the token for rollback.
- [ ] Rollback is setting both probe flags false and removing the scoped credential, restarting only if configuration reload requires it. Do not cancel already accepted broker work; revoke the credential on incident. No migration, systemd, or deployment change is required.
- [ ] Scope remains exact: no normal broker route, production selection/canary, persistence, or direct-path drift. Optional URL proof is conditional on operator URL and consent; authenticated synthetic proof is required.
- [ ] `@coder-heavy` self-verifies and invokes a fresh-context post-implementation reviewer. Post-review and human acceptance are required before closure. No visual review is needed.

## Deferred Work

Y02, requiring new human approval after Y01 approval, implementation, and review, owns any production routing selection/canary, durable terminal reconciliation/generation policy, cancellation, load behavior, and distributed exclusivity.
