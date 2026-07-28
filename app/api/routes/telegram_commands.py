from __future__ import annotations

from datetime import UTC, datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_internal_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.telegram_command_request import TelegramCommandRequest
from app.services.execution_lock import ExecutionLockBusy
from app.services.telegram_command_queue import TelegramCommandQueueService
from app.services.youtube_video_url import (
    YouTubeURLValidationError,
    parse_summary_command,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/internal/telegram-commands",
    tags=["telegram-commands"],
    dependencies=[Depends(require_internal_bearer_token)],
)


class TelegramCommandIntakeRequest(BaseModel):
    telegram_update_id: int
    telegram_chat_id: int
    telegram_user_id: int
    telegram_message_id: int
    telegram_chat_type: str
    telegram_update_type: str
    is_forwarded: bool = False
    is_edited: bool = False
    sender_chat_id: int | None = None
    text: str = Field(min_length=1, max_length=4096)


class TelegramCommandIntakeResponse(BaseModel):
    request_id: int | None = None
    accepted: bool
    duplicate: bool
    accepted_for_offset: bool
    status: str
    acknowledged_at: datetime | None = None
    acknowledgment_required: bool
    user_message: str
    outcome: str


class TelegramAcknowledgmentRequest(BaseModel):
    acknowledgment_message_id: int | None = None


class TelegramAcknowledgmentResponse(BaseModel):
    request_id: int
    acknowledged_at: datetime | None
    acknowledgment_message_id: int | None


class ErrorResponse(BaseModel):
    detail: str


class TelegramCommandWorkerResponse(BaseModel):
    claimed: bool
    request_id: int | None = None
    status: str
    work_remaining: bool


@router.post(
    "",
    response_model=TelegramCommandIntakeResponse,
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Accept an inbound Telegram summary command",
)
def accept_telegram_command(
    request: TelegramCommandIntakeRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> TelegramCommandIntakeResponse:
    if not settings.telegram_commands_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram commands are disabled.",
        )

    summary_command = _is_summary_command(request.text)
    submitted_url = _extract_submitted_url(request.text)
    existing = session.scalar(
        select(TelegramCommandRequest).where(
            TelegramCommandRequest.telegram_update_id == request.telegram_update_id,
        )
    )
    if existing is not None:
        return _duplicate_response(existing, request, submitted_url=submitted_url, summary_command=summary_command)

    _authorize_request(request, settings)
    if not summary_command:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported Telegram command.",
        )

    try:
        parsed = parse_summary_command(request.text, settings.normalized_telegram_bot_username)
    except YouTubeURLValidationError as exc:
        command_request = TelegramCommandRequest(
            telegram_update_id=request.telegram_update_id,
            telegram_chat_id=request.telegram_chat_id,
            telegram_user_id=request.telegram_user_id,
            telegram_message_id=request.telegram_message_id,
            command="summary",
            submitted_url=submitted_url,
            status="rejected",
            last_error=f"{exc.code}: {exc.message}",
        )
        session.add(command_request)
        duplicate_request = _flush_new_request(session, command_request)
        if duplicate_request is not None:
            return _duplicate_response(
                duplicate_request,
                request,
                submitted_url=submitted_url,
                summary_command=summary_command,
            )
        session.commit()
        return _intake_response(
            command_request,
            accepted=True,
            duplicate=False,
            accepted_for_offset=True,
            acknowledgment_required=False,
            user_message="No pude aceptar la URL. Revisa el formato de /summary.",
            outcome="rejected",
        )

    command_request = TelegramCommandRequest(
        telegram_update_id=request.telegram_update_id,
        telegram_chat_id=request.telegram_chat_id,
        telegram_user_id=request.telegram_user_id,
        telegram_message_id=request.telegram_message_id,
        command="summary",
        submitted_url=submitted_url,
        youtube_video_id=parsed.video.video_id,
        status="pending",
    )
    session.add(command_request)
    duplicate_request = _flush_new_request(session, command_request)
    if duplicate_request is not None:
        return _duplicate_response(
            duplicate_request,
            request,
            submitted_url=submitted_url,
            summary_command=summary_command,
        )
    session.commit()
    return _intake_response(
        command_request,
        accepted=True,
        duplicate=False,
        accepted_for_offset=True,
        acknowledgment_required=True,
        user_message="Recibido. Estoy generando el resumen...",
        outcome="accepted",
    )


@router.post(
    "/process-next",
    response_model=TelegramCommandWorkerResponse,
    responses={409: {"model": ErrorResponse}},
    summary="Process one queued Telegram command",
)
def process_next_telegram_command(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> TelegramCommandWorkerResponse:
    try:
        result = TelegramCommandQueueService(settings, session).process_next()
    except ExecutionLockBusy as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another command, polling, or reconciliation execution is active.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("Telegram command worker failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram command processing failed.",
        ) from exc

    return TelegramCommandWorkerResponse(
        claimed=result.claimed,
        request_id=result.request_id,
        status=result.status,
        work_remaining=result.work_remaining,
    )


@router.post(
    "/{request_id}/acknowledgment",
    response_model=TelegramAcknowledgmentResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Record a Telegram command acknowledgment",
)
def record_telegram_acknowledgment(
    request_id: int,
    request: TelegramAcknowledgmentRequest,
    session: Session = Depends(get_db_session),
) -> TelegramAcknowledgmentResponse:
    command_request = session.get(TelegramCommandRequest, request_id)
    if command_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram command request not found.")

    if command_request.acknowledged_at is None:
        command_request.acknowledged_at = datetime.now(UTC)
        command_request.acknowledgment_message_id = request.acknowledgment_message_id
        session.commit()

    return TelegramAcknowledgmentResponse(
        request_id=command_request.id,
        acknowledged_at=command_request.acknowledged_at,
        acknowledgment_message_id=command_request.acknowledgment_message_id,
    )


def _authorize_request(request: TelegramCommandIntakeRequest, settings: Settings) -> None:
    try:
        configured_chat_id = int(settings.telegram_chat_id)
        configured_user_id = int(settings.telegram_allowed_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Telegram command authorization is not configured.") from exc

    if (
        request.telegram_update_type != "message"
        or request.telegram_chat_type != "private"
        or request.telegram_chat_id != configured_chat_id
        or request.telegram_user_id != configured_user_id
        or request.is_forwarded
        or request.is_edited
        or request.sender_chat_id is not None
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram command is not authorized.")


def _is_summary_command(text: str) -> bool:
    first_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first_token.split("@", 1)[0] == "/summary"


def _extract_submitted_url(text: str) -> str | None:
    parts = text.strip().split()
    return parts[1][:4096] if len(parts) >= 2 else None


def _flush_new_request(
    session: Session,
    command_request: TelegramCommandRequest,
) -> TelegramCommandRequest | None:
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.telegram_update_id == command_request.telegram_update_id,
            )
        )
        if existing is None:
            raise
        return existing
    return None


def _duplicate_response(
    existing: TelegramCommandRequest,
    request: TelegramCommandIntakeRequest,
    *,
    submitted_url: str | None,
    summary_command: bool,
) -> TelegramCommandIntakeResponse:
    immutable_identity = (
        existing.telegram_chat_id == request.telegram_chat_id
        and existing.telegram_user_id == request.telegram_user_id
        and existing.telegram_message_id == request.telegram_message_id
        and existing.command == "summary" == ("summary" if summary_command else "")
        and existing.submitted_url == submitted_url
    )
    if not immutable_identity:
        logger.error("Telegram update ID replayed with conflicting immutable identity: update_id=%s", request.telegram_update_id)
        return _intake_response(
            existing,
            accepted=True,
            duplicate=True,
            accepted_for_offset=True,
            acknowledgment_required=False,
            user_message="",
            outcome="duplicate_conflict",
        )

    return _intake_response(
        existing,
        accepted=True,
        duplicate=True,
        accepted_for_offset=True,
        acknowledgment_required=existing.status != "rejected" and existing.acknowledged_at is None,
        user_message="Recibido. Estoy generando el resumen..." if existing.status != "rejected" else "",
        outcome="duplicate",
    )


def _intake_response(
    command_request: TelegramCommandRequest,
    *,
    accepted: bool,
    duplicate: bool,
    accepted_for_offset: bool,
    acknowledgment_required: bool,
    user_message: str,
    outcome: str,
) -> TelegramCommandIntakeResponse:
    return TelegramCommandIntakeResponse(
        request_id=command_request.id,
        accepted=accepted,
        duplicate=duplicate,
        accepted_for_offset=accepted_for_offset,
        status=command_request.status,
        acknowledged_at=command_request.acknowledged_at,
        acknowledgment_required=acknowledgment_required,
        user_message=user_message,
        outcome=outcome,
    )
