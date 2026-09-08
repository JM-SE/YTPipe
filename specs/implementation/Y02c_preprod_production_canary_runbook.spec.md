# Y02c Production Deployment and Canary Runbook

**Status:** ready_for_operator_approval. This runbook authorizes no deployment, database migration, restart, route change, service intervention, or canary until an operator gives a step-specific approval.

## Context

Y02c source implementation is committed on `main` as `b71b67b` (`feat Y02c resilient per-video recovery with crash-safe broker submission`); no deployment, migration, restart, or canary has occurred for it. Human implementation acceptance was granted after fresh-context review with no blocking findings.

There is no staging or preproduction environment, and no new preproduction infrastructure is in scope. Roll out directly to production using the existing Y02 canary approach. The declared topology is `systemd-native` (`Y02_broker_production_cutover.spec.md:140-144`). Production mutations and every privileged operation are manual-operator actions. `direct` remains independently supported and enabled throughout. `broker` is an approved canary route selected only through existing agnostic route configuration/data, never through a code branch; broker never automatically falls back to direct or restarts llama. Canary rollback switches affected canary traffic back to `direct`; it is not service removal.

## Dependencies / Inputs

- Source contracts: `Y02c_resilient_per_video_recovery.spec.md`, `Y02_broker_production_cutover.spec.md`, and `Y01_broker_safe_connectivity_acceptance.spec.md`.
- [ ] Immediately before production execution, the operator supplies and records the exact production host checkout/update and backup procedure, installed `ytpipe-api.service` unit details, DB backup/restore owner and approval, and local endpoint/host names. This is not a blocker to documenting or approving this runbook.
- [ ] Operator confirms deployed broker configuration and required secrets exist without printing values, and records explicit approval for each phase.
- [ ] `.env` is secret-bearing: do not read/copy it into outputs or commit it. If needed for a privileged interactive command, source it only locally without echoing values.
- [ ] `build/` is untracked/root-owned and excluded from `b71b67b`; do not remove it during rollout. Its optional cleanup is deferred hardening/packaging work.

## Operational Boundaries

| Responsibility | Boundary |
| --- | --- |
| Operator | Validates inputs and approvals; applies checkout, migration, restart, and route-data changes; decides abort/rollback; handles `sudo`. |
| Read-only verification | Health/readiness, status, logs, DB evidence queries, and broker policy status may be collected by automation, but must not change state or expose authorization, broker, or Telegram secrets. |
| Application/API | Runs Alembic through the deployed environment. Do not use direct DB SQL workarounds except under an explicitly approved incident procedure. |
| Scheduler | Preserve exactly one poll trigger; do not enable a second cron, timer, or unit. |

Never bundle deployment, migration, restart, route change, and canary activation into one unattended command.

## Privilege / Automation Classification

| Action | Privilege / approval | Evidence constraint |
| --- | --- | --- |
| Checkout/update command | Exact mechanism **UNKNOWN**; may not need `sudo`, but requires manual operator approval. | Record intended commit and resulting HEAD. |
| `alembic upgrade head` | May run as service account without `sudo`; production DB mutation requiring manual approval and backup evidence. | Confirm applied head. |
| `sudo systemctl daemon-reload`, `restart ytpipe-api.service`, scheduler-unit operations | Requires `sudo` per `systemd/README.md`; manual operator only. | Confirm installed unit and record status/log evidence. |
| Change route to `broker` or `direct` | May not require `sudo`; data/config mutation requiring manual approval. | Preserve auditable before/after route evidence. |
| Health/readiness/log/status/canary evidence collection | May be automated only as read-only collection. | Never print authorization, broker, or Telegram secrets. |

## Technical Approach

Use HOLD gates between all phases. Direct routing remains active through phases 0–1; broker routing begins only with the separately approved phase-2 canary. The known migration head is `20260907_0011`. `/ready` belongs to **llm-broker**, not YTPipe, and must be verified separately through the broker host/operator procedure.

### Template Commands — Operator Use Only

These are generic templates, not approval or execution instructions. Replace placeholders only from recorded operator inputs; do not use `set -x` or log secret-bearing headers/values.

```sh
# Read-only provenance templates
git rev-parse HEAD
git status --short

# Authorized interactive migration template: sources local secrets; do not echo values.
set -a; . ./.env; set +a; .venv/bin/alembic upgrade head

# Only after installed-unit confirmation and approval.
sudo systemctl restart ytpipe-api.service

# Read-only YTPipe health template.
curl -fsS http://127.0.0.1:8000/health

# Protected check template: keep the token out of shell tracing and outputs.
curl -fsS -H "Authorization: Bearer ${INTERNAL_API_BEARER_TOKEN}" \
  http://127.0.0.1:8000/<protected-status-or-reconcile-path>

# Read-only service-log template.
sudo journalctl -u ytpipe-api.service --since '<operator-recorded-time>' --no-pager

# Use a local/tool-specific, token-safe status command where required.
<operator-recorded-token-safe-status-command>
```

## Implementation Steps

1. **Phase 0 — Approval and non-mutating production preflight.** Record `b71b67b`, current `HEAD`, and clean intended checkout while excluding `build/`. Confirm no unresolved quarantines or indeterminates; explicitly resolve them with auditable evidence or block. Confirm the direct-route workload is processable, DB backup and recovery path, installed service ownership, one poll scheduler, and operator-provided endpoint/host names. Gather the exact host, checkout, unit, and backup procedure only immediately before execution. **HOLD:** stop without phase-0 approval.
2. **Phase 1 — Production release, all traffic `direct`.** With pre-backup evidence and approval, the operator updates the production checkout to `b71b67b`, applies the additive migration, and restarts only necessary systemd units. Verify YTPipe `/health`, authenticated `/status`, startup logs, and the single scheduler. Keep all traffic `direct` and verify direct-route workload remains processable after release. **HOLD:** require separate approval before phase 2.
3. **Phase 2 — Separately approved broker canary route activation.** With separate approval, change only existing agnostic route data/config for the scope governed by the Y02 specification to `broker`; do not change code or introduce a code branch. `direct` remains enabled and independently supported. **HOLD:** begin observation only after route-change evidence is recorded.
4. **Phase 3 — Production canary observation.** Observe for at least 12 hours with representative traffic. Reconcile YTPipe and broker outcomes one-to-one: each success has Telegram delivery; each terminal/quarantine failure has an explicit sanitized notice. Inspect quarantine and GET-only reconciliation evidence; verify no policy/digest drift, no broker-to-direct automatic fallback, and no direct llama restart.
5. **Phase 4 — Closure or data-plane rollback to direct.** Close only when all criteria pass. Abort immediately for any `broker_protocol_error`, unresolved indeterminate task, more than 20% `broker_output_incomplete` failures, policy-status drift, duplicate scheduler, opaque ID/key leak, unauthorized route fallback, or DB/service-health failure. With approval, roll back affected canary traffic by changing route data/config to `direct`; retain evidence and quarantine state, and verify direct-route workload remains processable. Do not remove or disable the direct route. Code rollback, migration downgrade, or restart each require separate approval; never automatically delete quarantine evidence.

## Edge Cases / Stop Conditions

- [ ] Known-task quarantine becomes GET-eligible only after an auditable operator reference; it must perform zero automatic POSTs while quarantined.
- [ ] No-ID quarantine remains blocked; do not infer an ID, create a key, submit, fall back, or restart llama.
- [ ] Stop and escalate on any abort criterion. Escalation recipient: **UNKNOWN — operator to fill**.

## Verification Plan

- [ ] Provenance is `b71b67b`; Alembic applied head is `20260907_0011`.
- [ ] After deployment, YTPipe `/health` returns 200 and authenticated status is acceptable; protected reconcile behavior is authenticated and token-safe.
- [ ] Broker route is `direct` in phases 0–1 and changes to `broker` only after separate phase-2 approval for the governing Y02 scope.
- [ ] There is one polling trigger, no broker-caused llama restart, and no broker-to-direct fallback.
- [ ] Canary runs for at least 12 hours with representative traffic, one-to-one activity reconciliation, and zero abort criteria.
- [ ] Successes have Telegram delivery; terminal/quarantine failures have explicit sanitized notices; quarantine is GET-only only after approved eligibility; no-ID remains blocked.
- [ ] Broker policy status and digest remain unchanged for the canary; broker `/ready` verification is separately recorded through its host/operator procedure.

## Rollback Plan

1. On a stop condition, with operator approval, roll back the **data plane first** by setting affected canary traffic in the agnostic route data/config to `direct`; verify that direct-route workload remains processable. Do not remove or disable the direct route.
2. Preserve all broker task, reconciliation, notice, and quarantine evidence. Do not delete, cancel, retry, resubmit, rotate keys, or automatically alter quarantined work.
3. Treat code checkout rollback, migration downgrade/database restoration, daemon reload, restart, and other service interventions as separate approved actions with their own evidence. DB restore owner: **UNKNOWN — operator to fill**. Escalation recipient: **UNKNOWN — operator to fill**.

## Acceptance Criteria

- [ ] Every phase has recorded, step-specific operator approval before its mutations.
- [ ] Required inputs, backup/recovery ownership, service ownership, scheduler singleton, and direct-route processability baseline are evidenced before production release.
- [ ] Production remains `direct` until separate canary approval; route transition is auditable data/config only and within the governing Y02 scope.
- [ ] Direct-route workload remains processable before deployment, after the production release, and after any canary rollback; no broker-to-direct automatic fallback is introduced.
- [ ] The canary meets the verification plan for at least 12 hours with representative traffic and no abort criteria.
- [ ] Rollback preserves broker/quarantine evidence and requires separate approval for code, database, or service intervention.
