#!/usr/bin/env bash
set -euo pipefail

state_dir="${YTPipe_MONITOR_STATE_DIR:-${HOME}/.local/state/ytpipe-poll-monitor}"
failure_file="$state_dir/last_failure_epoch"
failure_interval_seconds=21600
poll_timeout_seconds="${YTPipe_MONITOR_POLL_TIMEOUT_SECONDS:-3600}"
poll_interval_minutes="${POLL_INTERVAL_MINUTES:-60}"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != "--once" ]]; }; then
    printf 'Usage: %s [--once]\n' "$0" >&2
    exit 2
fi

if ! [[ "$poll_interval_minutes" =~ ^[1-9][0-9]*$ ]]; then
    printf 'POLL_INTERVAL_MINUTES must be a positive whole number\n' >&2
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

run_poll() {
    local now_epoch
    local last_failure_epoch=0

    now_epoch="$(date +%s)"
    if curl --silent --show-error --fail-with-body --max-time "$poll_timeout_seconds" \
        -X POST http://127.0.0.1:8000/internal/run-poll \
        -H "Authorization: Bearer ${INTERNAL_API_BEARER_TOKEN}" >/dev/null; then
        if [[ -f "$failure_file" ]]; then
            rm -f "$failure_file"
            send_telegram "YTPipe recuperado: el polling volvió a responder correctamente."
        fi
        return
    fi

    if [[ -f "$failure_file" ]]; then
        last_failure_epoch="$(<"$failure_file")"
    fi

    if (( now_epoch - last_failure_epoch >= failure_interval_seconds )); then
        printf '%s\n' "$now_epoch" >"$failure_file"
        send_telegram "⚠️ YTPipe: el polling falló o agotó el timeout. Revisá PostgreSQL, ytpipe-api y journalctl -u ytpipe-poll-monitor.service."
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
