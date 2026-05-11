from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_mobile_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.channel import Channel
from app.models.mobile_push_channel_preference import MobilePushChannelPreference
from app.models.mobile_push_installation import MobilePushInstallation
from app.models.mobile_push_setting import MobilePushSetting
from app.models.user import User
from app.models.user_channel import UserChannel
from app.services.mobile_push import MobilePushService, mask_expo_token

router = APIRouter(prefix="/internal/mobile-push", tags=["mobile-push"])


class ChannelPreferenceMonitoringFilter(str, Enum):
    monitored = "monitored"
    all = "all"


class ErrorResponse(BaseModel):
    detail: str


class MobilePushSettingsResponse(BaseModel):
    enabled: bool
    default_for_monitored_channels: bool
    first_enabled_at: str | None
    monitored_channels_effectively_enabled_count: int


class InstallationStatusResponse(BaseModel):
    registered: bool
    installation_id: UUID
    enabled: bool
    platform: str | None
    app_version: str | None
    build_number: str | None
    device_name: str | None
    token_masked: str | None
    registered_at: str | None
    last_seen_at: str | None
    unregistered_at: str | None
    invalidated_at: str | None
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None
    last_expo_ticket_id: str | None
    last_expo_status: str | None


class MobilePushStatusResponse(BaseModel):
    settings: MobilePushSettingsResponse
    installation: InstallationStatusResponse


class RegisterInstallationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "installation_id": "4c814f17-7a5f-4b87-82a6-7860387d02c1",
                "expo_push_token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
                "platform": "ios",
                "app_version": "1.0.0",
                "build_number": "1",
                "device_name": "iPhone",
            }
        }
    )

    installation_id: UUID
    expo_push_token: str = Field(min_length=1, max_length=2048)
    platform: str = Field(default="unknown", max_length=20)
    app_version: str | None = Field(default=None, max_length=50)
    build_number: str | None = Field(default=None, max_length=50)
    device_name: str | None = Field(default=None, max_length=120)


class RegisterInstallationResponse(BaseModel):
    registered: bool
    installation_id: UUID
    enabled: bool
    token_masked: str | None
    platform: str
    global_enabled: bool


class UnregisterInstallationResponse(BaseModel):
    registered: bool
    installation_id: UUID
    enabled: bool
    unregistered_at: str | None


class UpdateSettingsRequest(BaseModel):
    enabled: bool | None = None
    default_for_monitored_channels: bool | None = None


class ChannelPreferenceDetail(BaseModel):
    explicitly_set: bool
    push_enabled: bool | None


class ChannelPreferenceResponse(BaseModel):
    channel_id: int
    youtube_channel_id: str
    title: str | None
    is_monitored: bool
    push_eligible: bool
    push_enabled: bool
    preference: ChannelPreferenceDetail


class ChannelPreferencePagination(BaseModel):
    limit: int
    offset: int
    total: int


class ChannelPreferenceListResponse(BaseModel):
    channels: list[ChannelPreferenceResponse]
    pagination: ChannelPreferencePagination


class UpdateChannelPreferenceRequest(BaseModel):
    push_enabled: bool


class TestPushRequest(BaseModel):
    installation_id: UUID


class TestPushResponse(BaseModel):
    sent: bool
    event_type: str
    status: str
    message: str
    last_attempt_at: str | None
    expo_status: str | None
    expo_ticket_id: str | None


@router.get(
    "/status",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=MobilePushStatusResponse,
    summary="Get mobile push status",
)
def get_mobile_push_status(
    installation_id: UUID = Query(description="Mobile installation UUID."),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MobilePushStatusResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    push_settings = service.get_or_create_global_settings(session, user.id)
    installation = service.get_installation(session, user_id=user.id, installation_id=installation_id)
    session.commit()
    return MobilePushStatusResponse(
        settings=_serialize_settings(session, service, push_settings, user.id),
        installation=_serialize_installation_status(installation_id, installation),
    )


@router.post(
    "/register",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=RegisterInstallationResponse,
    summary="Register mobile push installation",
)
def register_installation(
    payload: RegisterInstallationRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RegisterInstallationResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    push_settings = service.get_or_create_global_settings(session, user.id)
    installation = service.register_installation(
        session,
        user_id=user.id,
        installation_id=payload.installation_id,
        expo_push_token=payload.expo_push_token,
        platform=payload.platform,
        app_version=payload.app_version,
        build_number=payload.build_number,
        device_name=payload.device_name,
    )
    session.commit()
    return RegisterInstallationResponse(
        registered=True,
        installation_id=installation.installation_id,
        enabled=installation.enabled,
        token_masked=mask_expo_token(installation.expo_push_token),
        platform=installation.platform,
        global_enabled=push_settings.enabled,
    )


@router.delete(
    "/installations/{installation_id}",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=UnregisterInstallationResponse,
    summary="Unregister mobile push installation",
)
def unregister_installation(
    installation_id: UUID = Path(description="Mobile installation UUID."),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UnregisterInstallationResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    installation = service.unregister_installation(session, user_id=user.id, installation_id=installation_id)
    session.commit()
    return UnregisterInstallationResponse(
        registered=installation is not None,
        installation_id=installation_id,
        enabled=False,
        unregistered_at=_dt(installation.unregistered_at) if installation else None,
    )


@router.patch(
    "/settings",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=MobilePushSettingsResponse,
    summary="Update global mobile push settings",
)
def update_mobile_push_settings(
    payload: UpdateSettingsRequest = Body(description="Partial mobile push settings update."),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MobilePushSettingsResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    push_settings = service.update_global_settings(
        session,
        user_id=user.id,
        enabled=payload.enabled,
        default_for_monitored_channels=payload.default_for_monitored_channels,
    )
    session.commit()
    return _serialize_settings(session, service, push_settings, user.id)


@router.get(
    "/channel-preferences",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=ChannelPreferenceListResponse,
    summary="List mobile push channel preferences",
)
def list_channel_preferences(
    monitoring: ChannelPreferenceMonitoringFilter = Query(default=ChannelPreferenceMonitoringFilter.monitored),
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ChannelPreferenceListResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    push_settings = service.get_or_create_global_settings(session, user.id)
    base_query = _channel_preference_base_query(user.id, monitoring, query)
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = session.execute(base_query.order_by(Channel.title.asc(), Channel.id.asc()).limit(limit).offset(offset)).all()
    response = ChannelPreferenceListResponse(
        channels=[_serialize_channel_preference(service, push_settings, user_channel, channel, preference) for user_channel, channel, preference in rows],
        pagination=ChannelPreferencePagination(limit=limit, offset=offset, total=total),
    )
    session.commit()
    return response


@router.patch(
    "/channels/{channel_id}",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=ChannelPreferenceResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Update mobile push channel preference",
)
def update_channel_preference(
    channel_id: int,
    payload: UpdateChannelPreferenceRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ChannelPreferenceResponse:
    user = _get_owner(session)
    channel = session.scalar(select(Channel).where(Channel.id == channel_id))
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")

    user_channel = session.scalar(
        select(UserChannel).where(UserChannel.user_id == user.id, UserChannel.channel_id == channel_id)
    )
    if user_channel is None or not user_channel.is_monitored:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Channel is not monitored.")

    service = MobilePushService(settings)
    push_settings = service.get_or_create_global_settings(session, user.id)
    preference = session.scalar(
        select(MobilePushChannelPreference).where(
            MobilePushChannelPreference.user_id == user.id,
            MobilePushChannelPreference.channel_id == channel_id,
        )
    )
    if preference is None:
        preference = MobilePushChannelPreference(user_id=user.id, channel_id=channel_id)
        session.add(preference)
    preference.push_enabled = payload.push_enabled
    preference.explicitly_set = True
    session.commit()
    return _serialize_channel_preference(service, push_settings, user_channel, channel, preference)


@router.post(
    "/test",
    dependencies=[Depends(require_mobile_bearer_token)],
    response_model=TestPushResponse,
    responses={409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Send mobile push test notification",
)
def send_test_push(
    payload: TestPushRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TestPushResponse:
    user = _get_owner(session)
    service = MobilePushService(settings)
    installation = service.get_installation(session, user_id=user.id, installation_id=payload.installation_id)
    if installation is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Installation is not registered for push notifications.")

    result = service.send_test_push(session, installation=installation)
    session.commit()
    if not result.sent:
        raise HTTPException(
            status_code=result.http_status_code or status.HTTP_502_BAD_GATEWAY,
            detail=result.message,
        )
    return TestPushResponse(
        sent=result.sent,
        event_type="test",
        status=result.status,
        message=result.message,
        last_attempt_at=_dt(result.last_attempt_at),
        expo_status=result.expo_status,
        expo_ticket_id=result.expo_ticket_id,
    )


def _get_owner(session: Session) -> User:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner user not found.")
    return user


def _serialize_settings(
    session: Session,
    service: MobilePushService,
    push_settings: MobilePushSetting,
    user_id: int,
) -> MobilePushSettingsResponse:
    return MobilePushSettingsResponse(
        enabled=push_settings.enabled,
        default_for_monitored_channels=push_settings.default_for_monitored_channels,
        first_enabled_at=_dt(push_settings.first_enabled_at),
        monitored_channels_effectively_enabled_count=_count_effectively_enabled_monitored_channels(
            session,
            service,
            push_settings,
            user_id,
        ),
    )


def _serialize_installation_status(
    installation_id: UUID,
    installation: MobilePushInstallation | None,
) -> InstallationStatusResponse:
    if installation is None:
        return InstallationStatusResponse(
            registered=False,
            installation_id=installation_id,
            enabled=False,
            platform=None,
            app_version=None,
            build_number=None,
            device_name=None,
            token_masked=None,
            registered_at=None,
            last_seen_at=None,
            unregistered_at=None,
            invalidated_at=None,
            last_attempt_at=None,
            last_success_at=None,
            last_error=None,
            last_expo_ticket_id=None,
            last_expo_status=None,
        )

    return InstallationStatusResponse(
        registered=installation.unregistered_at is None,
        installation_id=installation.installation_id,
        enabled=installation.enabled,
        platform=installation.platform,
        app_version=installation.app_version,
        build_number=installation.build_number,
        device_name=installation.device_name,
        token_masked=mask_expo_token(installation.expo_push_token),
        registered_at=_dt(installation.registered_at),
        last_seen_at=_dt(installation.last_seen_at),
        unregistered_at=_dt(installation.unregistered_at),
        invalidated_at=_dt(installation.invalidated_at),
        last_attempt_at=_dt(installation.last_attempt_at),
        last_success_at=_dt(installation.last_success_at),
        last_error=installation.last_error,
        last_expo_ticket_id=installation.last_expo_ticket_id,
        last_expo_status=installation.last_expo_status,
    )


def _channel_preference_base_query(user_id: int, monitoring: ChannelPreferenceMonitoringFilter, query: str | None):
    base_query = (
        select(UserChannel, Channel, MobilePushChannelPreference)
        .join(Channel, UserChannel.channel_id == Channel.id)
        .join(
            MobilePushChannelPreference,
            (MobilePushChannelPreference.user_id == UserChannel.user_id)
            & (MobilePushChannelPreference.channel_id == UserChannel.channel_id),
            isouter=True,
        )
        .where(UserChannel.user_id == user_id)
    )
    if monitoring == ChannelPreferenceMonitoringFilter.monitored:
        base_query = base_query.where(UserChannel.is_monitored.is_(True))
    if query and query.strip():
        normalized = query.strip().lower()
        base_query = base_query.where(
            or_(
                func.lower(Channel.title).contains(normalized),
                func.lower(Channel.youtube_channel_id).contains(normalized),
            )
        )
    return base_query


def _serialize_channel_preference(
    service: MobilePushService,
    push_settings: MobilePushSetting,
    user_channel: UserChannel,
    channel: Channel,
    preference: MobilePushChannelPreference | None,
) -> ChannelPreferenceResponse:
    state = service.compute_channel_push_state(push_settings, user_channel, preference)
    return ChannelPreferenceResponse(
        channel_id=channel.id,
        youtube_channel_id=channel.youtube_channel_id,
        title=channel.title,
        is_monitored=user_channel.is_monitored,
        push_eligible=state.push_eligible,
        push_enabled=state.push_enabled,
        preference=ChannelPreferenceDetail(
            explicitly_set=state.explicitly_set,
            push_enabled=state.explicit_push_enabled,
        ),
    )


def _count_effectively_enabled_monitored_channels(
    session: Session,
    service: MobilePushService,
    push_settings: MobilePushSetting,
    user_id: int,
) -> int:
    rows = session.execute(_channel_preference_base_query(user_id, ChannelPreferenceMonitoringFilter.monitored, None)).all()
    return sum(
        1
        for user_channel, _channel, preference in rows
        if service.compute_channel_push_state(push_settings, user_channel, preference).push_enabled
    )


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
