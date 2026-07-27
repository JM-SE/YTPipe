from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class LlamaRestartResult:
    attempted: bool
    succeeded: bool
    reason: str


class LlamaRecoveryService:
    """Request a narrowly scoped systemd restart without invoking a shell."""

    def __init__(
        self,
        *,
        enabled: bool,
        cooldown_seconds: int = 300,
        command_timeout_seconds: float = 30.0,
        sudo_path: str = "/usr/bin/sudo",
        systemctl_path: str = "/usr/bin/systemctl",
    ):
        self.enabled = enabled
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.command_timeout_seconds = max(1.0, command_timeout_seconds)
        self.sudo_path = sudo_path
        self.systemctl_path = systemctl_path

    def restart(self) -> LlamaRestartResult:
        if not self.enabled:
            return LlamaRestartResult(False, False, "Automatic llama.cpp restart is disabled.")

        command = [
            self.sudo_path,
            "-n",
            self.systemctl_path,
            "restart",
            "llama-server.service",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return LlamaRestartResult(True, False, "llama-server restart command timed out.")
        except OSError as exc:
            return LlamaRestartResult(True, False, f"llama-server restart command failed: {exc}")

        if completed.returncode == 0:
            return LlamaRestartResult(True, True, "llama-server.service restart requested.")

        detail = (completed.stderr or completed.stdout or "command returned a non-zero status").strip()
        detail = " ".join(detail.split())[:300]
        return LlamaRestartResult(True, False, f"llama-server restart failed: {detail}")
