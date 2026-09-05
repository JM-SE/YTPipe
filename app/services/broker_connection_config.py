from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from app.core.settings import Settings


class BrokerConfigurationError(ValueError):
    pass


MAX_BROKER_PROBE_TRANSCRIPT_CHARACTERS = 1_000_000
DEFAULT_BROKER_PROBE_TIMEOUT_SECONDS = 360.0
MAX_BROKER_PROBE_TIMEOUT_SECONDS = 360.0


@dataclass(frozen=True, slots=True)
class BrokerConnectionConfig:
    base_url: str
    bearer_token: str
    timeout_seconds: float


def broker_connection_config(settings: Settings) -> BrokerConnectionConfig:
    if not 1 <= settings.broker_probe_max_transcript_characters <= MAX_BROKER_PROBE_TRANSCRIPT_CHARACTERS:
        raise BrokerConfigurationError("Broker probe transcript cap is invalid.")
    base = settings.broker_base_url.strip()
    token = settings.broker_bearer_token
    if not base and not token:
        raise BrokerConfigurationError("Broker probe configuration is missing.")
    if not base or not token:
        raise BrokerConfigurationError("Broker probe configuration is incomplete.")
    if not token.strip() or token != token.strip() or any(c in token for c in "\r\n"):
        raise BrokerConfigurationError("Broker probe credential is invalid.")
    if token.casefold() in {"replace-me", "token", "bearer-token", "changeme"}:
        raise BrokerConfigurationError("Broker probe credential is invalid.")
    try:
        parsed = urlsplit(base)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise BrokerConfigurationError("Broker probe base URL is invalid.") from None
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise BrokerConfigurationError("Broker probe base URL is invalid.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or (port is not None and port not in {80, 443}):
        raise BrokerConfigurationError("Broker probe base URL is invalid.")
    app_env = settings.app_env.strip().lower()
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    loopback = host.casefold() == "localhost" or (address is not None and address.is_loopback)
    if app_env != "local" and (loopback or (address is not None and (address.is_private or address.is_link_local))):
        raise BrokerConfigurationError("Broker probe base URL origin is not allowed.")
    if parsed.scheme != "https" and not loopback:
        raise BrokerConfigurationError("Broker probe base URL must use HTTPS.")
    timeout = settings.broker_timeout_seconds
    if timeout is None:
        timeout = DEFAULT_BROKER_PROBE_TIMEOUT_SECONDS
    if not 1 <= float(timeout) <= MAX_BROKER_PROBE_TIMEOUT_SECONDS:
        raise BrokerConfigurationError("Broker probe timeout must be between 1 and 360 seconds.")
    return BrokerConnectionConfig(base.rstrip("/"), token, float(timeout))


def build_broker_client(config: BrokerConnectionConfig, *, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    timeout = httpx.Timeout(config.timeout_seconds, connect=config.timeout_seconds,
                            read=config.timeout_seconds, write=config.timeout_seconds,
                            pool=config.timeout_seconds)
    return httpx.Client(base_url=config.base_url, headers={"Authorization": f"Bearer {config.bearer_token}"},
                        timeout=timeout, follow_redirects=False, trust_env=False,
                        verify=True, transport=transport)
