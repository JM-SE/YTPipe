from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.notification_delivery import NotificationDelivery
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.services.polling import POLLING_PROCESS, QUOTA_PROCESS
from app.services.subscriptions import SUBSCRIPTION_SYNC_PROCESS

router = APIRouter(tags=["status"])


class ErrorResponse(BaseModel):
    detail: str


@router.get(
    "/status",
    dependencies=[Depends(require_admin_bearer_token)],
    responses={401: {"model": ErrorResponse}},
    summary="Get service operational status",
    description="Returns operational sync, polling, delivery, quota, and channel summary state.",
)
def status(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    user = session.scalar(select(User))
    sync_states = _load_sync_states(session, user.id) if user else {}
    delivery_summary = _build_email_status(session, user.id) if user else _empty_email_status()
    channel_summary = _build_channel_status(session, user.id) if user else {"imported_count": 0, "monitored_count": 0}
    quota_status = _build_quota_status(sync_states.get(QUOTA_PROCESS))

    ready = bool(settings.database_url) and user is not None and not quota_status.get("safety_stop_active", False)

    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "ready": ready,
        "subscription_sync": _build_sync_status(sync_states.get(SUBSCRIPTION_SYNC_PROCESS)),
        "polling": _build_polling_status(sync_states.get(POLLING_PROCESS)),
        "email": delivery_summary,
        "quota": quota_status,
        "channels": channel_summary,
    }


def _load_sync_states(session: Session, user_id: int) -> dict[str, SyncState]:
    states = session.scalars(select(SyncState).where(SyncState.user_id == user_id)).all()
    return {state.process_type: state for state in states}


def _build_sync_status(sync_state: SyncState | None) -> dict[str, object]:
    if sync_state is None:
        return {
            "last_success_at": None,
            "last_error_at": None,
            "last_error_message": None,
            "metadata": {},
        }

    return {
        "last_success_at": sync_state.last_success_at,
        "last_error_at": sync_state.last_error_at,
        "last_error_message": sync_state.last_error_message,
        "metadata": sync_state.state_metadata or {},
    }


def _build_polling_status(sync_state: SyncState | None) -> dict[str, object]:
    base = _build_sync_status(sync_state)
    metadata = base.pop("metadata")
    base["last_run"] = {
        "run_outcome": metadata.get("run_outcome"),
        "channels_processed": metadata.get("channels_processed", 0),
        "channels_failed": metadata.get("channels_failed", 0),
        "baselines_established": metadata.get("baselines_established", 0),
        "new_videos_detected": metadata.get("new_videos_detected", 0),
        "quota_blocked": metadata.get("quota_blocked", False),
        "channel_errors": metadata.get("channel_errors", []),
    }
    return base


def _build_email_status(session: Session, user_id: int) -> dict[str, object]:
    counts = {
        "pending": 0,
        "pending_retry": 0,
        "delivered": 0,
        "failed": 0,
    }
    count_rows = session.execute(
        select(NotificationDelivery.status, func.count(NotificationDelivery.id))
        .where(NotificationDelivery.user_id == user_id)
        .group_by(NotificationDelivery.status)
    ).all()
    for status_value, count in count_rows:
        if status_value in counts:
            counts[status_value] = count

    last_error_delivery = session.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.user_id == user_id, NotificationDelivery.last_error.is_not(None))
        .order_by(NotificationDelivery.last_attempt_at.desc().nullslast(), NotificationDelivery.id.desc())
        .limit(1)
    )

    return {
        "last_attempt_at": session.scalar(
            select(func.max(NotificationDelivery.last_attempt_at)).where(NotificationDelivery.user_id == user_id)
        ),
        "last_success_at": session.scalar(
            select(func.max(NotificationDelivery.last_attempt_at)).where(
                NotificationDelivery.user_id == user_id,
                NotificationDelivery.status == "delivered",
            )
        ),
        "last_failure_at": session.scalar(
            select(func.max(NotificationDelivery.last_attempt_at)).where(
                NotificationDelivery.user_id == user_id,
                NotificationDelivery.status.in_(("pending_retry", "failed")),
            )
        ),
        "last_error": last_error_delivery.last_error if last_error_delivery else None,
        "pending_count": counts["pending"],
        "pending_retry_count": counts["pending_retry"],
        "delivered_count": counts["delivered"],
        "failed_count": counts["failed"],
    }


def _empty_email_status() -> dict[str, object]:
    return {
        "last_attempt_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_error": None,
        "pending_count": 0,
        "pending_retry_count": 0,
        "delivered_count": 0,
        "failed_count": 0,
    }


def _build_quota_status(sync_state: SyncState | None) -> dict[str, Any]:
    metadata = sync_state.state_metadata if sync_state else {}
    metadata = metadata or {}
    return {
        "daily_quota_budget": metadata.get("daily_quota_budget"),
        "estimated_units_used_today": metadata.get("estimated_units_used_today", 0),
        "last_run_estimated_units": metadata.get("last_run_estimated_units", 0),
        "safety_stop_active": metadata.get("safety_stop_active", False),
        "safety_stop_enabled": metadata.get("safety_stop_enabled"),
        "safety_stop_triggered_at": metadata.get("safety_stop_triggered_at"),
    }


def _build_channel_status(session: Session, user_id: int) -> dict[str, int]:
    imported_count = session.scalar(select(func.count(UserChannel.id)).where(UserChannel.user_id == user_id)) or 0
    monitored_count = (
        session.scalar(
            select(func.count(UserChannel.id)).where(
                UserChannel.user_id == user_id,
                UserChannel.is_monitored.is_(True),
            )
        )
        or 0
    )
    return {"imported_count": imported_count, "monitored_count": monitored_count}
