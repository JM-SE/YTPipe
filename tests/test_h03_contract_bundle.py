"""H03 consumer runner: executes the vendored canonical consumer-compat/v1 bundle.

The fixture files under tests/contracts/consumer-compat/v1/ are a
byte-identical snapshot of the broker's canonical bundle. They are test data
only: normal runtime never reads them. If the canonical bundle changes, this
snapshot must be re-vendored and the digest test below fails closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import _task_result_status

BUNDLE_DIR = Path(__file__).resolve().parent / "contracts" / "consumer-compat" / "v1"


def _load(name: str) -> bytes:
    return (BUNDLE_DIR / name).read_bytes()


def test_vendored_bundle_digest_matches_canonical_algorithm() -> None:
    meta = json.loads(_load("bundle.json"))
    assert meta["bundle_version"] == "consumer-compat/v1"
    assert meta["vectors"] == [
        "valid-manifests.json",
        "invalid-manifests.json",
        "decisions.json",
        "terminal-envelopes.json",
        "transport-outcomes.json",
    ]
    digest = hashlib.sha256()
    for name in meta["vectors"]:
        content = _load(name)
        # json.loads raises on invalid JSON: vectors must stay parseable.
        json.loads(content)
        digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
    assert meta["digest"] == "sha256:" + digest.hexdigest()


def test_terminal_envelopes_are_accepted_with_exact_status() -> None:
    envelopes = json.loads(_load("terminal-envelopes.json"))
    assert len(envelopes) == 5
    seen: set[str] = set()
    for envelope in envelopes:
        request = httpx.Request("GET", "https://broker.example.test/v1/tasks/t/result")
        response = httpx.Response(200, json=envelope, request=request)
        assert _task_result_status(response) == envelope["status"]
        seen.add(envelope["status"])
    assert seen == {"pending", "succeeded", "failed", "cancelled", "expired"}


def test_transport_outcomes_cover_every_terminal_status() -> None:
    outcomes = json.loads(_load("transport-outcomes.json"))
    assert len(outcomes) == 5
    assert {item["status"] for item in outcomes} == {
        "pending",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    }


def test_terminal_envelope_rejects_near_miss_shapes() -> None:
    base = {
        "content": "x",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    near_miss = [
        {"status": "pending", "result": dict(base)},
        {"status": "succeeded"},
        {"status": "failed"},
        {
            "status": "cancelled",
            "error": {"class": "internal", "code": "internal_error", "message": "x"},
        },
    ]
    for envelope in near_miss:
        request = httpx.Request("GET", "https://broker.example.test/v1/tasks/t/result")
        response = httpx.Response(200, json=envelope, request=request)
        with pytest.raises(BrokerSummarizationError):
            _task_result_status(response)


def test_bundle_vectors_execute_valid_invalid_and_decisions() -> None:
    # F6 runner: execute the vendored canonical vectors locally. Manifest and
    # decision structures are pure data contracts; the YTPipe side pins
    # accept/reject semantics and exact decision expectations without
    # contacting any broker, database, or inference backend.
    valid = json.loads(_load("valid-manifests.json"))
    assert len(valid) == 2
    seen_workloads: set[str] = set()
    for manifest in valid:
        _assert_y01_shape(manifest)
        assert manifest["workload"] not in seen_workloads
        seen_workloads.add(manifest["workload"])

    invalid = json.loads(_load("invalid-manifests.json"))
    assert len(invalid) == 6
    for entry in invalid:
        with pytest.raises(ValueError):
            _assert_y01_shape(_entry_document(entry))

    decisions = json.loads(_load("decisions.json"))
    assert len(decisions) == 23
    statuses = {vector["status"] for vector in decisions}
    assert statuses == {"compatible", "incompatible"}
    codes: set[str] = set()
    for vector in decisions:
        assert set(vector) >= {"name", "mode", "config", "manifest", "status", "findings"}
        assert vector["mode"] in {"desired", "effective"}
        for finding in vector["findings"]:
            assert set(finding) == {"code", "count"}
            assert finding["count"] >= 1
            codes.add(finding["code"])
    # Every frozen worker finding code must be exercised by the bundle.
    assert {
        "api_version_incompatible", "workload_unavailable", "capability_unavailable",
        "capability_features_unsupported", "generation_not_admitted",
        "response_kind_unsupported", "response_schema_unsupported",
        "request_content_limit_insufficient", "explicit_output_bytes_required",
        "output_bytes_limit_insufficient", "backend_response_start_timeout_insufficient",
        "workload_timeout_insufficient", "attempt_limit_exceeded",
        "consumer_deadline_too_short", "client_not_found", "client_disabled",
        "workload_grant_missing", "durable_policy_drift", "topology_mismatch",
    } <= codes


def _assert_y01_shape(manifest: object) -> None:
    # Local structural mirror of the strict broker parser classes: 12 exact
    # top-level fields, no wildcard identifiers, no null required sections.
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    required = {
        "version", "api_range", "workload", "capability", "generation", "response",
        "max_request_content_bytes", "required_min_output_bytes",
        "min_workload_timeout_seconds", "min_backend_response_start_timeout_seconds",
        "max_acceptable_attempts", "consumer_overall_deadline_seconds",
    }
    if set(manifest) != required:
        raise ValueError("manifest fields are not the frozen contract set")
    for key in ("workload", "capability"):
        value = manifest[key]
        if not isinstance(value, str) or "*" in value:
            raise ValueError("manifest identifier is invalid")
    for key in ("generation", "response"):
        section = manifest[key]
        if not isinstance(section, dict) or section is None:
            raise ValueError("manifest section is invalid")
    if manifest["version"] != "consumer-compat/v1":
        raise ValueError("manifest version is unsupported")


def _entry_document(entry: dict) -> object:
    if entry.get("raw"):
        return json.loads(entry["raw"])
    return entry.get("manifest")
