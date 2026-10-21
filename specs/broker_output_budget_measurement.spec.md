# Broker output-budget measurement

YTPipe's approved broker consumer contract now requests up to 2048 output tokens. This is a coordinated consumer-contract amendment, not evidence that 2048 is optimal. Verified activation and initial post-change coverage are recorded below. Direct llama.cpp defaults remain 1024. The consumer accepts up to three internal broker attempts, matching the existing `batch-summary.max_attempts=3` policy; this alignment does not change YTPipe stage counters or enable retries of previously terminal outputs. Measure outcomes before drawing conclusions; do not automatically replay previously failed stages or regenerate stored summaries.

## Pre-change baseline

Read-only observations from 2026-10-21T04:35:35.967442Z covered the last seven days, selecting the last persisted task for each summary stage with a 200-stage cap. All 28 selected stages had retained results: 26 ended with `stop`, two with `length`; no chunked stages were present. This is a stage-level sample, not a census of every broker operation.

| Measure | Observed baseline |
|---|---:|
| Completion tokens, all retained results | p50 681; p95 1024 (censored) |
| `stop` completion tokens | p50 679; p95 931; max 1003 |
| Prompt tokens | p50 6342; p95 12228; max 12898 |
| End-to-end task duration | p50 61.177 s; p95 142.644 s; max 155.783 s |
| UTF-8 output bytes | p50 2755; p95 3737; max 4054 |
| Length-terminated results | 2/28 (7.14%) |

The duration is derived from task timestamps and includes queueing plus inference; it is not pure model latency. A `length` result means the requested output exceeded the old ceiling, so its required completion length is censored. Consequently the all-results p95 is censored at 1024 and successful-only percentiles are biased downward. The two observed truncated tasks had prompts of 6076 and 6342 tokens, 1024 completion tokens, durations of 63.766 and 66.376 seconds, and output sizes of 3683 and 4054 bytes. The sample cannot establish their latent required lengths or predict that 2048 will suffice.

The largest observed prompt plus a 2048-token output is 14946 tokens, below the 16384-token context by 1438 tokens. That arithmetic is not a guarantee for future inputs: tokenization, prompt overhead, and planner margin still matter. It does not establish model quality or the optimal output ceiling.

## Reproducible read-only method

Use the existing read-only database selection: summary stages with `broker_task_id`, last seven days, latest persisted task per stage, capped at 200. For each selected task, read broker `GET /v1/tasks/{id}/result` and `GET /v1/tasks/{id}`; retained broker results live for seven days. Do not submit inference requests. Count selected stages as the coverage denominator, and report missing or expired results separately. Exclude missing/expired results from token percentiles, but never silently remove them from coverage. This historical selection does not guarantee coverage of all intermediate operations; separate those only when existing records identify them.

Aggregate only: completion and prompt tokens, finish reason, task end-to-end duration from timestamps, UTF-8 output bytes, timeout/error classifications, and model/prompt/config versions when already available. Never output task IDs, idempotency keys, transcripts, summaries, secrets, or other per-task identifying content. Do not add flags or assume metadata that existing records do not contain.

## Post-change assessment

Compare non-overlapping pre- and post-activation windows using the same prompt, model, and temperature. Include dense and long-content cases, report coverage and missing/expired results, and review summary fidelity with a human. Track completion tokens, finish reasons, prompt tokens, end-to-end duration, output bytes, timeout/error categories, and available version identifiers. Report whether the sample is informative; no fixed observation period or acceptance count is prescribed here.

A larger sample improves precision but does not prove universal behavior. For intuition only, zero truncations in 60 independent, representative cases gives an approximate 95% upper bound of 5% on the underlying truncation rate (rule of three); it is not a guarantee or a universal threshold. Do not claim 2048 efficacy until post-activation observations exist, and distinguish truncation reduction from fidelity, latency, and operational outcomes.

## Verified activation and first snapshot (2026-10-21)

The source work unit is `f4454cc`. Independent operational verification confirmed that only the two `batch-summary` token limits changed from 1024 to 2048, with `max_attempts=3` and every other config field unchanged. The rollback backup is `/etc/llm-broker/config.json.before-ytpipe-2048-01a15c6a`. Durable policy reports zero pending changes; effective compatibility for client `ytpipe` matches the approved consumer profile. The five relevant services are active; health and admin readiness checks returned HTTP 200.

The restarted API's `ActiveEnterTimestamp`, **2026-10-21T22:02:58Z**, is the post-change traffic boundary. The runner began at 22:02:35.502406Z, before draining and cutover; do not attribute that transition interval to the new configuration.

| First post-change snapshot | Observation |
|---|---|
| Window (UTC) | 2026-10-21T22:02:58Z to 22:16:16Z |
| Retained summary stages scanned in the rolling seven-day pool | 26 (selection cap 200) |
| Post-boundary candidates from the conservative stage-timestamp bound | 0 |
| Retained last broker tasks attributable to the post-change window | 0 observed |
| Task/result GETs for this sample | 0; no candidates required fetching (result cap 5 unused) |
| Finish reasons, tokens, durations, truncation rate | Not estimable: no post-change tasks |

The 26 scanned stages are not 26 new cases: the post-change denominator is zero. Do not report a zero-percent truncation rate or compare this empty window against the baseline's 2/28. This stage-level view is not a census of intermediate or orphaned broker operations. The snapshot verifies coverage only; it establishes no efficacy, optimality, or sufficiency of 2048. Later assessment uses the read-only method above and comparable input/window coverage, without replaying failed stages.
