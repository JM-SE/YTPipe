# Architectural Risks Follow-Up

## Context

YTPipe is currently running as a single-process homelab service with one API instance, one local `llama-server`, one PostgreSQL database, and one local systemd timer that triggers polling. That topology is acceptable for the current deployment, and the recent recovery work left the system healthy.

This document records architectural risks that are not urgent for the current setup but should be addressed before running multiple API workers, multiple app instances, a containerized multi-process deployment, or stronger exactly-once notification guarantees.

## Current Assumptions

- Only one YTPipe API process is expected to run at a time.
- Only one local `llama-server` is expected to serve inference.
- `ytpipe-poll-monitor.timer` is the only scheduled poll trigger.
- The local process-level locks are effective only inside the current Python process.
- Telegram delivery is treated as operationally reliable but not exactly-once.

## Risk 1: Process-Local Locks

### Current State

The poll/reconciliation execution lock and summary inference lock are implemented with in-process `threading.Lock` objects.

These locks protect only threads inside one Python process. They do not coordinate across:

- multiple uvicorn worker processes
- multiple API instances
- separate containers
- overlapping manual scripts using the same database
- a restart window where old and new processes overlap briefly

### Failure Scenario

If two app processes run at the same time, both can independently acquire their own local lock and start work. They can select the same pending `pipeline_stages`, summarize the same video, and attempt the same Telegram delivery.

### Impact

- duplicate summaries
- duplicate Telegram messages
- unnecessary local model load or inference pressure
- race conditions while updating `pipeline_stages`
- inconsistent operational state if one process commits and another fails

### Recommended Mitigations

1. Use a PostgreSQL advisory lock for poll/reconciliation execution.
2. Use row-level work claiming for `pipeline_stages` or pending videos.
3. Ensure every worker claims a job inside a transaction before performing external work.
4. Keep process-local locks only as an additional intra-process optimization, not the primary coordination mechanism.

### Acceptance Criteria For A Future Fix

- Two concurrent `POST /internal/run-poll` calls cannot both perform work.
- Two separate Python processes cannot summarize the same pending video simultaneously.
- The system returns a clear conflict/skip result when work is already claimed elsewhere.

## Risk 2: Telegram Delivery Is At-Least-Once

### Current State

Telegram delivery happens before the `PipelineStage` status is durably committed as `completed`.

The simplified sequence is:

1. Generate or load summary.
2. Send Telegram message.
3. Mark `telegram` stage as `completed`.
4. Commit database transaction.

### Failure Scenario

If Telegram accepts the message but the process crashes, the host reboots, or PostgreSQL fails before the DB commit, the stage may remain `pending` or `pending_retry`. On the next run, the system retries and sends the Telegram message again.

### Impact

- rare duplicate Telegram notifications
- user confusion after crash/recovery events
- harder incident auditing because Telegram side effects are not fully represented in DB

### Recommended Mitigations

1. Add a delivery/outbox table for Telegram messages.
2. Persist an intended Telegram send before contacting Telegram.
3. Use a dispatcher that claims outbox rows and records provider response metadata.
4. Store a deterministic idempotency key per `user_id`, `video_id`, and delivery type.
5. If Telegram cannot provide external idempotency, make duplicate detection internal and surface possible duplicate risk in operations.

### Acceptance Criteria For A Future Fix

- A crash after Telegram accepts a message does not cause an automatic duplicate send without explicit operator intervention.
- Every attempted Telegram send has a durable DB record with status, timestamp, and error/provider metadata.
- Summary Telegram and fallback Telegram have separate idempotency keys.

## Risk 3: No Cross-Process Job Claiming

### Current State

Pending work is discovered by querying `pipeline_stages` with statuses such as `pending` and `pending_retry`. There is no explicit `claimed`, `processing`, `lease_owner`, or `lease_expires_at` state.

### Failure Scenario

Two workers can read the same pending stage before either updates it. Both can do expensive or externally visible work, then race to update the row.

### Impact

- duplicate model calls
- duplicate notifications
- wasted GPU/CPU time
- potential starvation or retries if a process dies mid-stage and no lease model exists

### Recommended Mitigations

1. Add claim fields to `pipeline_stages`, for example:
   - `claimed_at`
   - `claimed_by`
   - `lease_expires_at`
2. Claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction.
3. Process only rows successfully claimed by the current worker.
4. Expire stale claims safely after a timeout.
5. Record processing attempts separately from final status transitions.

### Acceptance Criteria For A Future Fix

- Concurrent workers select distinct pending videos/stages.
- A crashed worker does not permanently strand a stage.
- Retrying stale claims is deterministic and visible in diagnostics.

## Risk 4: Long-Running Poll Requests

### Current State

The poll endpoint can now drain pending videos sequentially. This is operationally useful for backlog recovery, but it means a `POST /internal/run-poll` can take much longer than a simple channel scan when there is pending pipeline work.

### Failure Scenario

A monitor, reverse proxy, scheduler, or client timeout can expire while the API continues processing in the background. This already happened during backlog recovery: the client timed out while the server continued successfully.

### Impact

- false failure alerts if timeout is too low
- uncertainty about whether a poll is still running
- difficult manual operation when a single request represents both polling and backlog drain

### Recommended Mitigations

1. Split scan/discovery from drain execution more explicitly.
2. Add an internal drain endpoint or command with clear long-running semantics.
3. Persist a run record with `started_at`, `finished_at`, `status`, and progress counters.
4. Have the monitor distinguish between API unavailable and long-running active work.

### Acceptance Criteria For A Future Fix

- Operators can see whether a drain is active without reading logs.
- Monitor timeout is aligned with the longest expected healthy operation.
- A timed-out client does not create ambiguity about server-side progress.

## Risk 5: Single-Host Operational Coupling

### Current State

The homelab setup intentionally keeps API, PostgreSQL, local inference, monitor, and scheduler on one host.

This is simple and appropriate now, but operational coupling means host-level failures affect every component.

### Failure Scenario

A host reboot, Docker failure, systemd unit failure, or GPU/inference issue can block multiple parts of the pipeline at once.

Recent recovery work reduced this risk by:

- setting PostgreSQL Docker restart policy to `unless-stopped`
- restoring `llama-server.service` under systemd
- replacing bare cron with monitored systemd timer
- adding Telegram alerts from outside the API poll logic

### Remaining Impact

- if the user-level or system-level monitor cannot run, failures may still be silent
- if Telegram itself is unavailable, local alerts may fail
- if PostgreSQL is unavailable, application-level state cannot be updated

### Recommended Mitigations

1. Periodically verify systemd timers and service health.
2. Keep host boot/reboot checklists updated.
3. Add a lightweight local health-report command or script.
4. Consider a second notification path only if Telegram outages become a practical issue.

## Suggested Implementation Order

1. Add PostgreSQL advisory lock for poll/reconciliation execution.
2. Add durable Telegram outbox/idempotency records.
3. Add row-level stage claiming with lease expiry.
4. Split long-running drain visibility from normal polling status.
5. Extend diagnostics to show active lock/lease/run state.

## Non-Goals For Current Homelab

- Multi-user support.
- Celery, Redis, or a new queueing stack.
- Multiple notification channels unless explicitly approved later.
- Changing normal latest-upload polling semantics.
- Replacing the local single-host architecture without a separate decision.
