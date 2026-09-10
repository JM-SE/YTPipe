from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/ytpipe-poll-monitor.sh")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _install_fake_curl(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "curl",
        r'''#!/usr/bin/env bash
set -euo pipefail
state_dir="$FAKE_CURL_STATE"

for argument in "$@"; do
  if [[ "$argument" == https://api.telegram.org/* ]]; then
    for message_argument in "$@"; do
      if [[ "$message_argument" == text=* ]]; then
        printf 'telegram:%s\n' "${message_argument#text=}" >>"$state_dir/requests.log"
      fi
    done
    exit 0
  fi
done

if [[ "$*" == *"/internal/run-poll"* ]]; then
  printf 'poll\n' >>"$state_dir/requests.log"
  case "$(<"$state_dir/poll_mode")" in
    connection)
      printf 'curl: (7) connection refused\n' >&2
      exit 7
      ;;
    other)
      printf 'curl: (28) operation timed out\n' >&2
      exit 28
      ;;
    success)
      printf '{"run_outcome":"success"}\n'
      exit 0
      ;;
  esac
fi

if [[ "$*" == *"/status"* ]]; then
  printf 'status\n' >>"$state_dir/requests.log"
  case "$(<"$state_dir/status_mode")" in
    ready)
      printf '{"ready":true}\n'
      exit 0
      ;;
    not_ready)
      printf '{"ready":false}\n'
      exit 0
      ;;
    connection)
      exit 7
      ;;
  esac
fi

printf 'unexpected curl request\n' >&2
exit 2
''',
    )


def _run_monitor(
    tmp_path: Path,
    *,
    poll_mode: str,
    status_mode: str = "ready",
    recovery_interval: str = "1",
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    monitor_state = tmp_path / "monitor-state"
    monitor_state.mkdir(exist_ok=True)
    (state_dir / "poll_mode").write_text(f"{poll_mode}\n", encoding="utf-8")
    (state_dir / "status_mode").write_text(f"{status_mode}\n", encoding="utf-8")
    _install_fake_curl(fake_bin)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_CURL_STATE": str(state_dir),
            "YTPipe_MONITOR_STATE_DIR": str(monitor_state),
            "YTPipe_MONITOR_POLL_TIMEOUT_SECONDS": "1",
            "YTPipe_MONITOR_RECOVERY_PROBE_INTERVAL_SECONDS": recovery_interval,
            "POLL_INTERVAL_MINUTES": "60",
            "INTERNAL_API_BEARER_TOKEN": "test-token",
            "TELEGRAM_NOTIFICATIONS_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_CHAT_ID": "123",
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT), "--once"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _requests(tmp_path: Path) -> list[str]:
    path = tmp_path / "state" / "requests.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_connection_failure_probes_readiness_and_notifies_recovery_immediately(tmp_path: Path) -> None:
    result = _run_monitor(tmp_path, poll_mode="connection")

    assert result.returncode != 0
    requests = _requests(tmp_path)
    assert requests.count("poll") == 1
    assert requests.count("status") == 1
    assert sum(request.startswith("telegram:") for request in requests) == 2
    telegram_messages = [request for request in requests if request.startswith("telegram:")]
    assert "la API y la base de datos volvieron a responder" in telegram_messages[1]
    assert not (tmp_path / "monitor-state" / "last_failure_epoch").exists()


def test_non_connection_failure_does_not_probe_or_claim_recovery(tmp_path: Path) -> None:
    result = _run_monitor(tmp_path, poll_mode="other")

    assert result.returncode != 0
    requests = _requests(tmp_path)
    assert requests.count("poll") == 1
    assert "status" not in requests
    assert sum(request.startswith("telegram:") for request in requests) == 1
    assert (tmp_path / "monitor-state" / "last_failure_epoch").exists()


def test_repeated_failure_keeps_alert_suppression(tmp_path: Path) -> None:
    first = _run_monitor(tmp_path, poll_mode="other")
    second = _run_monitor(tmp_path, poll_mode="other")

    assert first.returncode != 0
    assert second.returncode != 0
    requests = _requests(tmp_path)
    assert requests.count("poll") == 2
    assert sum(request.startswith("telegram:") for request in requests) == 1


def test_recovery_probe_interval_must_be_positive(tmp_path: Path) -> None:
    result = _run_monitor(tmp_path, poll_mode="success", recovery_interval="0")

    assert result.returncode == 2
    assert "must be a positive whole number" in result.stderr
    assert _requests(tmp_path) == []
