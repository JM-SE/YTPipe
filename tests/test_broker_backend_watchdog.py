from __future__ import annotations

from datetime import UTC, datetime, timedelta

import app.services.polling as polling_module
from app.core.settings import Settings
from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.video import Video
from app.services.pipeline import PipelineService
from app.services.polling import SUMMARIZATION_PROCESS, YouTubePollingService
from app.services.telegram import TelegramDeliveryService


def _settings() -> Settings:
    return Settings(
        APP_SECRET_KEY="super-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
    )


class _FakeRecovery:
    """Counts restart calls without touching systemd."""

    def __init__(self, *, enabled: bool = True, cooldown_seconds: int = 300) -> None:
        self.enabled = enabled
        self.cooldown_seconds = cooldown_seconds
        self.calls = 0

    def restart(self):
        self.calls += 1
        return type("Result", (), {"succeeded": True, "reason": "restart requested"})()


def _make_video(db_session, user, channel, youtube_video_id: str) -> Video:
    video = Video(
        youtube_video_id=youtube_video_id,
        channel_id=channel.id,
        title=f"Video {youtube_video_id}",
    )
    db_session.add(video)
    db_session.flush()
    return video


def _make_summary_stage(
    db_session,
    user,
    video,
    *,
    status: str,
    failure_class: str | None = None,
    failure_code: str | None = None,
    updated_at=None,
) -> PipelineStage:
    stage = PipelineStage(
        video_id=video.id,
        user_id=user.id,
        stage="summary",
        status=status,
        failure_class=failure_class,
        failure_code=failure_code,
        last_error="El backend de inferencia no pudo completar la solicitud.",
    )
    db_session.add(stage)
    db_session.flush()
    if updated_at is not None:
        stage.updated_at = updated_at
        db_session.flush()
    return stage


def _make_service(monkeypatch, settings, restart_service):
    pipeline_service = PipelineService()
    telegram_service = TelegramDeliveryService(settings)
    sent_messages: list[str] = []
    monkeypatch.setattr(telegram_service, "send_message", lambda text: sent_messages.append(text))
    monkeypatch.setattr(polling_module, "summarization_route_name", lambda service: "broker")
    service = YouTubePollingService(
        auth_service=object(),
        email_service=object(),
        daily_quota_budget=100,
        safety_stop_enabled=True,
        telegram_service=telegram_service,
        pipeline_service=pipeline_service,
        llama_recovery_service=restart_service,
    )
    return service, sent_messages


def _make_user_state(db_session):
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="UC-WATCHDOG", title="Watchdog Channel")
    db_session.add_all([user, channel])
    db_session.flush()
    state = SyncState(user_id=user.id, process_type=SUMMARIZATION_PROCESS)
    db_session.add(state)
    db_session.flush()
    return user, channel, state


def test_triggers_restart_and_alert_at_threshold(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    for youtube_video_id in ("backend-error-1", "backend-error-2"):
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class="transient_safe",
            failure_code="backend_error",
            updated_at=now - timedelta(minutes=30),
        )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 1
    assert len(sent_messages) == 1
    assert "backend de inferencia" in sent_messages[0]
    assert "restart requested" in sent_messages[0]
    incident = state.state_metadata["broker_backend_incident"]
    assert incident["failure_code"] == "backend_error"
    assert incident["failure_count"] == 2
    assert incident["restart_succeeded"] is True
    assert incident["alert_sent"] is True


def test_second_finalize_within_cooldown_does_not_restart_again(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    for youtube_video_id in ("backend-error-1", "backend-error-2"):
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class="transient_safe",
            failure_code="backend_error",
            updated_at=now - timedelta(minutes=30),
        )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))
    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 1
    assert len(sent_messages) == 1


def test_below_threshold_records_without_restart_or_alert(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    video = _make_video(db_session, user, channel, "backend-error-1")
    _make_summary_stage(
        db_session,
        user,
        video,
        status="pending_retry",
        failure_class="transient_safe",
        failure_code="backend_error",
        updated_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 0
    assert sent_messages == []
    incident = state.state_metadata["broker_backend_incident"]
    assert incident["failure_count"] == 1
    assert "restart_attempted_at" not in incident


def test_rejected_and_context_exceeded_never_trigger(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    cases = (
        ("rejected-video", "backend_rejected", "backend_rejected"),
        ("context-video", "backend_rejected", "backend_context_exceeded"),
    )
    for youtube_video_id, failure_class, failure_code in cases:
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class=failure_class,
            failure_code=failure_code,
            updated_at=now - timedelta(minutes=30),
        )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 0
    assert sent_messages == []
    assert "broker_backend_incident" not in (state.state_metadata or {})
    assert state.last_success_at is not None
    assert state.last_error_at is None


def test_success_after_errors_clears_incident(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    for youtube_video_id in ("backend-error-1", "backend-error-2"):
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class="transient_safe",
            failure_code="backend_error",
            updated_at=now - timedelta(hours=2),
        )
    recovered_video = _make_video(db_session, user, channel, "recovered-video")
    _make_summary_stage(
        db_session,
        user,
        recovered_video,
        status="completed",
        updated_at=now - timedelta(minutes=10),
    )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 0
    assert sent_messages == []
    assert "broker_backend_incident" not in (state.state_metadata or {})


def test_old_failures_outside_window_are_ignored(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    for youtube_video_id in ("backend-error-1", "backend-error-2"):
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class="transient_safe",
            failure_code="backend_error",
            updated_at=now - timedelta(hours=5),
        )
    db_session.commit()

    restart_service = _FakeRecovery()
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 0
    assert sent_messages == []
    assert "broker_backend_incident" not in (state.state_metadata or {})


def test_disabled_records_alert_but_does_not_execute(db_session, monkeypatch) -> None:
    settings = _settings()
    user, channel, state = _make_user_state(db_session)
    now = datetime.now(UTC)
    for youtube_video_id in ("backend-error-1", "backend-error-2"):
        video = _make_video(db_session, user, channel, youtube_video_id)
        _make_summary_stage(
            db_session,
            user,
            video,
            status="failed",
            failure_class="transient_safe",
            failure_code="backend_error",
            updated_at=now - timedelta(minutes=30),
        )
    db_session.commit()

    restart_service = _FakeRecovery(enabled=False)
    service, sent_messages = _make_service(monkeypatch, settings, restart_service)

    service._finalize_summary_circuit(db_session, user, state, datetime.now(UTC))

    assert restart_service.calls == 0
    assert len(sent_messages) == 1
    assert "no configurado" in sent_messages[0]
    assert "restart_attempted_at" not in state.state_metadata["broker_backend_incident"]
