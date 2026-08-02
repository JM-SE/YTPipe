from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import unquote_plus


SCRIPT = Path("scripts/ytpipe-llama-monitor.sh")
UNIT = Path("systemd/ytpipe-llama-monitor.service")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_monitor(tmp_path: Path, fake_state: Path, *, state_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{environment['PATH']}",
            "HOME": str(tmp_path),
            "FAKE_SYSTEMCTL_STATE": str(fake_state),
            "FAKE_CURL_STATE": str(fake_state),
            "YTPipe_LLAMA_MONITOR_STATE_DIR": str(state_dir),
            "TELEGRAM_NOTIFICATIONS_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "test-token",
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


def _alert_messages(fake_state: Path) -> list[str]:
    log_file = fake_state / "telegram.log"
    if not log_file.exists():
        return []
    return [unquote_plus(line.removeprefix("text=")) for line in log_file.read_text().splitlines()]


def _install_fake_commands(fake_bin: Path, *, curl_handles_telegram: bool = True) -> None:
    _write_executable(
        fake_bin / "systemctl",
        r'''#!/usr/bin/env bash
set -euo pipefail
state_dir="$FAKE_SYSTEMCTL_STATE"
case "$1" in
  is-active)
    [[ "$(<"$state_dir/active")" == "active" ]]
    ;;
  show)
    property=""
    for argument in "$@"; do
      case "$argument" in
        --property=MainPID) property="MainPID" ;;
        --property=ExecMainStartTimestampMonotonic) property="ExecMainStartTimestampMonotonic" ;;
      esac
    done
    cat "$state_dir/$property"
    ;;
esac
''',
    )
    curl_telegram_branch = r'''
for argument in "$@"; do
  if [[ "$argument" == https://api.telegram.org/* ]]; then
    for message_argument in "$@"; do
      if [[ "$message_argument" == text=* ]]; then
        printf '%s\n' "$message_argument" >>"$state_dir/telegram.log"
      fi
    done
    exit 0
  fi
done
'''
    curl_telegram_branch = curl_telegram_branch if curl_handles_telegram else ""
    _write_executable(
        fake_bin / "curl",
        f'''#!/usr/bin/env bash
set -euo pipefail
state_dir="$FAKE_CURL_STATE"
{curl_telegram_branch}
if [[ "$(<"$state_dir/active")" != "active" ]]; then
  exit 22
fi
printf '%s\n' '{{"models":[{{"id":"test-model"}}]}}'
''',
    )


def test_monitor_alerts_once_on_failure_and_again_on_recovery(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_state = tmp_path / "fake-state"
    fake_state.mkdir()
    state_dir = tmp_path / "monitor-state"

    (fake_state / "active").write_text("active\n")
    (fake_state / "MainPID").write_text("100\n")
    (fake_state / "ExecMainStartTimestampMonotonic").write_text("1000\n")
    _install_fake_commands(fake_bin)

    first = _run_monitor(tmp_path, fake_state, state_dir=state_dir)
    assert first.returncode == 0
    assert _alert_messages(fake_state) == []

    (fake_state / "active").write_text("inactive\n")
    failed = _run_monitor(tmp_path, fake_state, state_dir=state_dir)
    assert failed.returncode != 0
    assert len(_alert_messages(fake_state)) == 1
    assert "no está saludable" in _alert_messages(fake_state)[0]

    repeated = _run_monitor(tmp_path, fake_state, state_dir=state_dir)
    assert repeated.returncode != 0
    assert len(_alert_messages(fake_state)) == 1

    (fake_state / "active").write_text("active\n")
    (fake_state / "MainPID").write_text("200\n")
    (fake_state / "ExecMainStartTimestampMonotonic").write_text("2000\n")
    recovered = _run_monitor(tmp_path, fake_state, state_dir=state_dir)
    assert recovered.returncode == 0
    assert len(_alert_messages(fake_state)) == 2
    assert "recuperado correctamente" in _alert_messages(fake_state)[1]


def test_monitor_detects_a_fast_restart_while_healthy(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_state = tmp_path / "fake-state"
    fake_state.mkdir()
    state_dir = tmp_path / "monitor-state"

    (fake_state / "active").write_text("active\n")
    (fake_state / "MainPID").write_text("100\n")
    (fake_state / "ExecMainStartTimestampMonotonic").write_text("1000\n")
    _install_fake_commands(fake_bin)

    assert _run_monitor(tmp_path, fake_state, state_dir=state_dir).returncode == 0
    (fake_state / "MainPID").write_text("200\n")
    (fake_state / "ExecMainStartTimestampMonotonic").write_text("2000\n")

    restarted = _run_monitor(tmp_path, fake_state, state_dir=state_dir)
    assert restarted.returncode == 0
    messages = _alert_messages(fake_state)
    assert len(messages) == 2
    assert "se reinició" in messages[0]
    assert "recuperado correctamente" in messages[1]


def test_monitor_systemd_unit_is_unprivileged_and_contains_no_secrets() -> None:
    unit = UNIT.read_text(encoding="utf-8")

    assert "User=jmse" in unit
    assert "EnvironmentFile=/home/jmse/labs/YTPipe/.env" in unit
    assert "After=network-online.target llama-server.service" in unit
    assert "Restart=on-failure" in unit
    assert "ExecStart=/home/jmse/labs/YTPipe/scripts/ytpipe-llama-monitor.sh" in unit
    assert "TELEGRAM_BOT_TOKEN=" not in unit
    assert "TELEGRAM_CHAT_ID=" not in unit
