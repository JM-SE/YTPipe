from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


def create_signed_state(secret_key: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    payload = {
        "nonce": nonce,
        "issued_at": int(time.time()),
    }
    encoded_payload = _urlsafe_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign_payload(secret_key, encoded_payload)
    return f"{encoded_payload}.{signature}", nonce


def verify_signed_state(state: str, secret_key: str, max_age_seconds: int = 600) -> dict[str, Any]:
    try:
        encoded_payload, signature = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed OAuth state value.") from exc

    expected_signature = _sign_payload(secret_key, encoded_payload)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid OAuth state signature.")

    payload = json.loads(_urlsafe_decode(encoded_payload))
    issued_at = payload.get("issued_at")
    if not isinstance(issued_at, int):
        raise ValueError("OAuth state is missing a valid issue timestamp.")

    if int(time.time()) - issued_at > max_age_seconds:
        raise ValueError("OAuth state has expired.")

    return payload


def _sign_payload(secret_key: str, encoded_payload: str) -> str:
    digest = hmac.new(secret_key.encode("utf-8"), encoded_payload.encode("utf-8"), hashlib.sha256).digest()
    return _urlsafe_encode(digest)


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")
