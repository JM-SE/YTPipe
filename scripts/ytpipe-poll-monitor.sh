#!/usr/bin/env bash
set -euo pipefail

state_dir="${YTPipe_MONITOR_STATE_DIR:-${HOME}/.local/state/ytpipe-poll-monitor}"
failure_file="$state_dir/last_failure_epoch"
failure_interval_seconds=21600
poll_timeout_seconds="${YTPipe_MONITOR_POLL_TIMEOUT_SECONDS:-3600}"
poll_interval_minutes="${POLL_INTERVAL_MINUTES:-60}"
recovery_probe_interval_seconds="${YTPipe_MONITOR_RECOVERY_PROBE_INTERVAL_SECONDS:-15}"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != "--once" ]]; }; then
    printf 'Usage: %s [--once]\n' "$0" >&2
    exit 2
fi

if ! [[ "$poll_interval_minutes" =~ ^[1-9][0-9]*$ ]]; then
    printf 'POLL_INTERVAL_MINUTES must be a positive whole number\n' >&2
    exit 2
fi

if ! [[ "$recovery_probe_interval_seconds" =~ ^[1-9][0-9]*$ ]]; then
    printf 'YTPipe_MONITOR_RECOVERY_PROBE_INTERVAL_SECONDS must be a positive whole number\n' >&2
    exit 2
fi

poll_interval_seconds=$((poll_interval_minutes * 60))

mkdir -p "$state_dir"

if [[ -z "${INTERNAL_API_BEARER_TOKEN:-}" ]]; then
    printf 'INTERNAL_API_BEARER_TOKEN is required\n' >&2
    exit 2
fi

send_telegram() {
    local message="$1"
    if [[ "${TELEGRAM_NOTIFICATIONS_ENABLED:-false}" != "true" ]] || [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] || [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
        return
    fi

    curl --silent --show-error --fail --max-time 30 \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${message}" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null || true
}

api_is_ready() {
    local status_response

    status_response="$(curl --silent --fail --max-time 10 \
        http://127.0.0.1:8000/status \
        -H "Authorization: Bearer ${INTERNAL_API_BEARER_TOKEN}")" || return 1
    [[ "$status_response" =~ \"ready\"[[:space:]]*:[[:space:]]*true ]]
}

wait_for_api_recovery() {
    while ! api_is_ready; do
        sleep "$recovery_probe_interval_seconds"
    done
}

notify_recovery_if_needed() {
    if [[ -f "$failure_file" ]]; then
        rm -f "$failure_file"
        send_telegram "YTPipe recuperado: la API y la base de datos volvieron a responder. El próximo polling seguirá su horario normal."
    fi
}

run_poll() {
    local now_epoch
    local last_failure_epoch=0
    local poll_failure_reason
    local poll_exit_code=0

    now_epoch="$(date +%s)"
    poll_failure_reason="$(curl --silent --show-error --fail-with-body --max-time "$poll_timeout_seconds" \
        -X POST http://127.0.0.1:8000/internal/run-poll \
        -H "Authorization: Bearer ${INTERNAL_API_BEARER_TOKEN}" 2>&1)" || poll_exit_code=$?
    if (( poll_exit_code == 0 )); then
        notify_recovery_if_needed
        return
    fi

    if [[ -f "$failure_file" ]]; then
        last_failure_epoch="$(<"$failure_file")"
    fi

    if (( now_epoch - last_failure_epoch >= failure_interval_seconds )); then
        printf '%s\n' "$now_epoch" >"$failure_file"
        poll_failure_reason="${poll_failure_reason//$'\n'/ }"
        poll_failure_reason="${poll_failure_reason//$'\r'/ }"
        poll_failure_reason="${poll_failure_reason:0:300}"
        if [[ -z "$poll_failure_reason" ]]; then
            poll_failure_reason="sin detalle devuelto por curl"
        fi
        send_telegram "⚠️ YTPipe: el polling falló o agotó el timeout. Causa: ${poll_failure_reason}. Revisá PostgreSQL, ytpipe-api y journalctl -u ytpipe-poll-monitor.service."
    fi

    if (( poll_exit_code == 7 )); then
        wait_for_api_recovery
        notify_recovery_if_needed
    fi

    return 1
}

if (( $# == 1 )); then
    run_poll
    exit 0
fi

while true; do
    run_poll || true
    sleep "$poll_interval_seconds"
done
