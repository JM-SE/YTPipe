from __future__ import annotations

import subprocess

from app.services.llama_recovery import LlamaRecoveryService


def test_restart_uses_exact_non_shell_command(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("app.services.llama_recovery.subprocess.run", fake_run)

    result = LlamaRecoveryService(enabled=True).restart()

    assert result.succeeded is True
    assert calls[0]["command"] == [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemctl",
        "restart",
        "llama-server.service",
    ]
    assert calls[0]["shell"] is False


def test_restart_disabled_does_not_execute(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.llama_recovery.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected restart")),
    )

    result = LlamaRecoveryService(enabled=False).restart()

    assert result.attempted is False
    assert result.succeeded is False
