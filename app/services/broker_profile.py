from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


PROFILE_PATH = Path(__file__).parents[1] / "contracts" / "broker" / "y01.consumer-compat.json"
PROFILE_VERSION = "consumer-compat/v1"
Y01_ACCEPTED_FINISH_REASON = "stop"


@dataclass(frozen=True, slots=True)
class BrokerRequestProfile:
    version: str
    api_range: str
    workload: str
    capability: str
    max_tokens: int
    temperature: float
    response_kind: str
    max_request_content_bytes: int
    required_min_output_bytes: int
    min_workload_timeout_seconds: int
    min_backend_response_start_timeout_seconds: int
    max_acceptable_attempts: int
    consumer_overall_deadline_seconds: int
    digest: str
    accepted_finish_reason: str = Y01_ACCEPTED_FINISH_REASON


def load_y01_profile(path: Path = PROFILE_PATH) -> BrokerRequestProfile:
    try:
        raw = path.read_bytes()
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Broker consumer profile is invalid.") from exc
    _validate_document(document)
    canonical = _canonical_document(document)
    digest = "sha256:" + sha256(canonical).hexdigest()
    generation = document["generation"]
    response = document["response"]
    return BrokerRequestProfile(
        version=document["version"],
        api_range=" ".join(document["api_range"].split()),
        workload=document["workload"],
        capability=document["capability"],
        max_tokens=generation["max_tokens"],
        temperature=generation["temperature"],
        response_kind=response["kind"],
        max_request_content_bytes=document["max_request_content_bytes"],
        required_min_output_bytes=document["required_min_output_bytes"],
        min_workload_timeout_seconds=document["min_workload_timeout_seconds"],
        min_backend_response_start_timeout_seconds=document[
            "min_backend_response_start_timeout_seconds"
        ],
        max_acceptable_attempts=document["max_acceptable_attempts"],
        consumer_overall_deadline_seconds=document["consumer_overall_deadline_seconds"],
        digest=digest,
    )


def _validate_document(document: Any) -> None:
    # F11: the frozen literals below are intentional. This loader derives
    # every request value from the tracked manifest (single source), but the
    # validator additionally pins the exact H03-accepted Y01 profile so any
    # manifest substitution fails closed instead of silently changing the
    # probe contract.
    required = {
        "version", "api_range", "workload", "capability", "generation", "response",
        "max_request_content_bytes", "required_min_output_bytes", "min_workload_timeout_seconds",
        "min_backend_response_start_timeout_seconds", "max_acceptable_attempts",
        "consumer_overall_deadline_seconds",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("Broker consumer profile is invalid.")
    if document["version"] != PROFILE_VERSION or document["api_range"] != ">=0.1.1 <0.2.0":
        raise ValueError("Broker consumer profile is invalid.")
    for key in ("workload", "capability"):
        value = document[key]
        if not isinstance(value, str) or not value or value != value.lower() or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in value
        ):
            raise ValueError("Broker consumer profile is invalid.")
    generation = document["generation"]
    if not isinstance(generation, dict) or set(generation) != {"max_tokens", "temperature"}:
        raise ValueError("Broker consumer profile is invalid.")
    if (
        type(generation["max_tokens"]) is not int
        or generation["max_tokens"] < 1
        or type(generation["temperature"]) not in {int, float}
        or not 0 <= generation["temperature"] <= 2
    ):
        raise ValueError("Broker consumer profile is invalid.")
    response = document["response"]
    if not isinstance(response, dict) or set(response) != {"kind"} or response["kind"] not in {
        "text", "json_object", "json_schema"
    }:
        raise ValueError("Broker consumer profile is invalid.")
    positive = (
        "max_request_content_bytes", "required_min_output_bytes", "max_acceptable_attempts",
        "consumer_overall_deadline_seconds",
    )
    nonnegative = ("min_workload_timeout_seconds", "min_backend_response_start_timeout_seconds")
    if any(type(document[key]) is not int or document[key] < 1 for key in positive):
        raise ValueError("Broker consumer profile is invalid.")
    if any(type(document[key]) is not int or document[key] < 0 for key in nonnegative):
        raise ValueError("Broker consumer profile is invalid.")
    if (
        document["version"] != PROFILE_VERSION
        or document["api_range"] != ">=0.1.1 <0.2.0"
        or document["workload"] != "batch-summary"
        or document["capability"] != "summarize"
        or generation["max_tokens"] != 1024
        or generation["temperature"] != 0.7
        or response["kind"] != "text"
        or document["max_request_content_bytes"] != 131072
        or document["required_min_output_bytes"] != 16384
        or document["min_workload_timeout_seconds"] != 300
        or document["min_backend_response_start_timeout_seconds"] != 60
        or document["max_acceptable_attempts"] != 1
        or document["consumer_overall_deadline_seconds"] != 360
    ):
        raise ValueError("Broker consumer profile is invalid.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate key")
        document[key] = value
    return document


def _canonical_document(document: dict[str, Any]) -> bytes:
    # Field order mirrors the broker's normalized manifest struct exactly
    # (Go json.Marshal emits declaration order, not sorted keys). The digest
    # is a cross-language correlation token, so Python must serialize the
    # identical bytes: compact separators, ensure_ascii=False, no escaping.
    generation = document["generation"]
    pairs: list[tuple[str, Any]] = [
        ("version", document["version"]),
        ("api_range", " ".join(document["api_range"].split())),
        ("workload", document["workload"]),
        ("capability", document["capability"]),
        # The broker canonicalizes generation via a Go map (sorted keys).
        ("generation", dict(sorted(generation.items()))),
        ("response", {"kind": document["response"]["kind"]}),
        ("max_request_content_bytes", document["max_request_content_bytes"]),
        ("required_min_output_bytes", document["required_min_output_bytes"]),
        ("min_workload_timeout_seconds", document["min_workload_timeout_seconds"]),
        ("min_backend_response_start_timeout_seconds", document[
            "min_backend_response_start_timeout_seconds"
        ]),
        ("max_acceptable_attempts", document["max_acceptable_attempts"]),
        ("consumer_overall_deadline_seconds", document["consumer_overall_deadline_seconds"]),
    ]
    return json.dumps(dict(pairs), ensure_ascii=False, separators=(",", ":")).encode()
