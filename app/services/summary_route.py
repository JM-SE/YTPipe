from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from app.core.settings import Settings
from app.services.broker_connection_config import broker_connection_config, build_broker_client
from app.services.broker_gateway import BrokerTaskClient
from app.services.broker_profile import load_y01_profile
from app.services.broker_traffic import BrokerTrafficSummarizationService
from app.services.direct_summarization import build_summarization_gateway

if TYPE_CHECKING:
    from app.services.summarization_gateway import SummarizationGateway

logger = logging.getLogger(__name__)

DIRECT_ROUTE = "direct"
BROKER_ROUTE = "broker"


def resolve_summary_route(value: str | None) -> Literal["direct", "broker"]:
    """Resolve the inference route from raw `SUMMARY_ROUTE` data.

    Pure function: exact values `direct` / `broker` (surrounding whitespace
    tolerated); absent or unrecognized data resolves to the safe default
    `direct`. Callers own the attribution log.
    """
    if value is None:
        return DIRECT_ROUTE
    normalized = value.strip()
    if normalized == BROKER_ROUTE:
        return BROKER_ROUTE
    return DIRECT_ROUTE


def summarization_route_name(service: object) -> Literal["direct", "broker"]:
    """Attribution-only route query for an already-wired gateway service.

    Pure `isinstance` check used by the per-summarization attribution record
    in the pipeline. It never constructs clients, touches configuration, or
    performs IO; unknown service types resolve to `direct`.
    """
    if isinstance(service, BrokerTrafficSummarizationService):
        return BROKER_ROUTE
    return DIRECT_ROUTE


def close_routed_summarization_gateway(service: object) -> None:
    """Release transport resources held by a routed gateway service.

    No-op for services without a `close` method (the direct gateway holds no
    transport). Every composition root calls this in a `finally` block so the
    broker `httpx.Client` is closed once per request/command/run instead of
    leaking one client per polling request or Telegram command.
    """
    close = getattr(service, "close", None)
    if callable(close):
        close()


def build_routed_summarization_gateway(
    settings: Settings, *, root: str
) -> SummarizationGateway:
    """Single wiring point for direct-vs-broker inference routing.

    Every composition root calls this helper instead of
    `build_summarization_gateway` directly. One supplemental record is logged
    per call with route plus root identifier only — video/stage identifiers
    do not exist at wiring time (startup and polling build once per
    process/run), so the root name is the stable identifier and carries no
    prompts, content, credentials, or broker topology. Per-summarization
    attribution (route plus stage/video identifiers) is logged by the
    pipeline at inference time; see `summarization_route_name`.

    The `broker` branch fails closed: invalid profile/broker configuration
    propagates instead of silently falling back to direct inference.
    """
    route = resolve_summary_route(settings.summary_route or None)
    logger.info("summary_route_resolved root=%s route=%s", root, route)
    if route != BROKER_ROUTE:
        return build_summarization_gateway(settings)
    profile = load_y01_profile()
    config = broker_connection_config(settings)
    client = build_broker_client(config)
    task_client = BrokerTaskClient.from_client(client, timeout=config.timeout_seconds, profile=profile)
    return BrokerTrafficSummarizationService(task_client, profile=profile)
