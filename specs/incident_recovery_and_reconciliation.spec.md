# Incident Recovery And Reconciliation Specification

## Context

Normal polling reads only the latest upload for each monitored channel. This is intentional for the single-user deployment: successful polling is expected to advance each channel's marker without needing history scans. A service or database outage can invalidate that assumption and requires a controlled, explicit reconciliation operation.

## Requirements

- [ ] Keep normal `POST /internal/run-poll` latest-upload-only behavior unchanged.
- [ ] Provide an authenticated administrative reconciliation operation that pages uploads backwards from the current latest upload until it reaches each channel's `last_seen_video_id`.
- [ ] Do not scan or import a channel's full history when its stored marker is absent from the scanned uploads; report that channel for manual review instead.
- [ ] Persist all discovered missing videos and their pipeline stages before processing content.
- [ ] Process recovered videos oldest-first and process only one video at a time.
- [ ] Allow at most one local llama.cpp summary inference at a time across normal polling, retry pickup, startup processing, and reconciliation.
- [ ] Drain pending videos oldest-first in one execution, committing each video before a configurable 60-second pause.
- [ ] Reconciliation must be resumable and idempotent through the existing video and pipeline-stage uniqueness constraints.
- [ ] Expose pipeline failures, pending retries, missing summaries, and fallback delivery failures through an authenticated diagnostic endpoint.
- [ ] Record unexpected pipeline exceptions rather than silently ignoring them.
- [ ] Add an operational check outside the database-backed application path that alerts Telegram after polling HTTP failures, database unavailability, or timeouts, and sends a recovery alert after the next successful poll.
- [ ] When a summary inference fails during polling, persist a summarization circuit breaker, alert Telegram with the sanitized cause, continue transcript extraction, and leave summary/Telegram stages pending.
- [ ] When configured, request one cooldown-protected restart of `llama-server.service` through a narrowly scoped host privilege rule, and resume summaries only after a later real inference succeeds.
- [ ] Keep operational secrets out of application source. The existing scheduler token rotation is an operator-managed follow-up and is out of scope for this incident recovery.

## Reconciliation Contract

1. An operator invokes reconciliation only after an incident or a deliberate historical recovery.
2. For each monitored channel with a baseline, fetch uploads in playlist order, newest to oldest, until `last_seen_video_id` is found.
3. The uploads after that marker are the recovery window. Persist them oldest to newest, skipping already canonical `Video` rows.
4. If the marker is not found before the configured page limit, do not advance the marker or import an unbounded history for that channel. Return a channel-level reconciliation error.
5. After durable persistence, process pending stages one video at a time. A video advances through transcript, summary, Telegram, and fallback handling before another video's summary may begin.
6. Advance `last_seen_video_id` only after the recovery window has been durably persisted.
7. A later invocation resumes from persisted videos and stages without duplicate notification attempts.
8. The default drain pause is 60 seconds. It is between videos, not between pipeline stages, and does not reduce the model context or output-token budget.
9. The local llama.cpp server uses one slot with a 16k context and a 1024-token output budget. Prompt-cache retention is disabled to bound memory use; long transcripts use sequential partial summaries plus a final synthesis rather than truncation.

## Operational Recovery

1. Back up the PostgreSQL volume before recovery work.
2. PostgreSQL must restart automatically after host reboot.
3. The poll scheduler must treat non-2xx responses and timeouts as failures.
4. The external monitor stores its throttling state outside PostgreSQL so it can alert while PostgreSQL is unavailable.
5. The monitor must not expose bearer tokens or Telegram credentials in logs.

## Acceptance Criteria

- [ ] A normal poll still reads only `maxResults=1` for each channel.
- [ ] Reconciliation discovers every upload between a stored marker and the latest upload within the configured bound.
- [ ] Recovered videos are processed oldest-first with one summary inference at a time.
- [ ] Restarting reconciliation creates no duplicate videos, pipeline stages, or Telegram summaries.
- [ ] A missing marker is surfaced and does not advance channel state.
- [ ] Unexpected pipeline failures are visible in durable diagnostics.
- [ ] A stopped PostgreSQL container causes a throttled Telegram alert from the host monitor.
- [ ] The next successful poll causes a Telegram recovery alert.
- [ ] A llama.cpp failure pauses later summary attempts without losing newly fetched transcripts.
- [ ] A successful post-restart inference closes the summarization circuit and drains pending summaries.
