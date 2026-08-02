#!/usr/bin/env bash
set -euo pipefail

state_dir="${YTPipe_LLAMA_MONITOR_STATE_DIR:-${HOME}/.local/state/ytpipe-llama-monitor}"
state_file="$state_dir/state"
service_name="${YTPipe_LLAMA_SERVICE_NAME:-llama-server.service}"
base_url="${YTPipe_LLAMA_MONITOR_BASE_URL:-http://127.0.0.1:8001}"
interval_seconds="${YTPipe_LLAMA_MONITOR_INTERVAL_SECONDS:-10}"
request_timeout_seconds="${YTPipe_LLAMA_MONITOR_REQUEST_TIMEOUT_SECONDS:-5}"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != "--once" ]]; }; then
    printf 'Usage: %s [--once]\n' "$0" >&2
    exit 2
fi

if ! [[ "$interval_seconds" =~ ^[1-9][0-9]*$ ]]; then
    printf 'YTPipe_LLAMA_MONITOR_INTERVAL_SECONDS must be a positive whole number\n' >&2
    exit 2
fi

if ! [[ "$request_timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
    printf 'YTPipe_LLAMA_MONITOR_REQUEST_TIMEOUT_SECONDS must be a positive whole number\n' >&2
    exit 2
fi

mkdir -p "$state_dir"

health_url="${base_url%/}/health"
models_url="${base_url%/}/v1/models"

monitor_state="unknown"
monitor_identity=""

load_state() {
    monitor_state="unknown"
    monitor_identity=""
    if [[ ! -f "$state_file" ]]; then
        return
    fi

    local -a values=()
    mapfile -t values <"$state_file"
    monitor_state="${values[0]:-unknown}"
    monitor_identity="${values[1]:-}"
}

save_state() {
    local state="$1"
    local identity="$2"
    local temporary_file="$state_file.$$"

    printf '%s\n%s\n' "$state" "$identity" >"$temporary_file"
    mv -f -- "$temporary_file" "$state_file"
}

send_telegram() {
    local message="$1"

    if [[ "${TELEGRAM_NOTIFICATIONS_ENABLED:-false}" != "true" ]] \
        || [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] \
        || [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
        return 0
    fi

    if ! curl --silent --show-error --fail --max-time 30 \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${message}" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null; then
        printf 'Telegram alert delivery failed.\n' >&2
    fi
}

service_identity() {
    local main_pid
    local start_timestamp

    main_pid="$(systemctl show "$service_name" --property=MainPID --value 2>/dev/null || true)"
    start_timestamp="$(systemctl show "$service_name" --property=ExecMainStartTimestampMonotonic --value 2>/dev/null || true)"

    if [[ -z "$main_pid" && -z "$start_timestamp" ]]; then
        return
    fi
    printf '%s|%s\n' "$main_pid" "$start_timestamp"
}

server_is_healthy() {
    systemctl is-active --quiet "$service_name" || return 1
    curl --silent --show-error --fail --max-time "$request_timeout_seconds" "$health_url" >/dev/null \
        || return 1
    curl --silent --show-error --fail --max-time "$request_timeout_seconds" "$models_url" \
        | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(1)

models = payload.get("models") if isinstance(payload, dict) else None
raise SystemExit(0 if isinstance(models, list) and bool(models) else 1)
'
}

check_once() {
    local current_identity=""
    local healthy=0

    load_state
    if server_is_healthy; then
        healthy=1
        current_identity="$(service_identity)"

        if [[ "$monitor_state" == "unhealthy" ]]; then
            send_telegram "✅ YTPipe: llama-server recuperado correctamente. Servicio activo, /health OK y modelo cargado."
        elif [[ "$monitor_state" == "healthy" \
            && -n "$monitor_identity" \
            && -n "$current_identity" \
            && "$monitor_identity" != "$current_identity" ]]; then
            send_telegram "⚠️ YTPipe: llama-server se reinició. Verificando que vuelva a funcionar correctamente."
            send_telegram "✅ YTPipe: llama-server recuperado correctamente. Servicio activo, /health OK y modelo cargado."
        fi

        save_state "healthy" "$current_identity"
    else
        if [[ "$monitor_state" != "unhealthy" ]]; then
            send_telegram "⚠️ YTPipe: llama-server no está saludable. Se detectó una caída o reinicio; esperando recuperación."
        fi
        save_state "unhealthy" ""
    fi

    (( healthy == 1 ))
}

if (( $# == 1 )); then
    check_once
    exit $?
fi

while true; do
    check_once || true
    sleep "$interval_seconds"
done
