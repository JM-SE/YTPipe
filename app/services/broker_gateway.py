from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import httpx

from app.services.broker_errors import broker_error
from app.services.broker_summary import validate_broker_output
from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation, idempotency_key
from app.services.summarization_planner import plan_operations


class BrokerSummarizationGateway:
    """Dormant B00 client. Construction requires an explicitly injected transport."""

    def __init__(self, *, base_url: str, credential: str, transport: httpx.BaseTransport,
                 timeout: float, max_tokens: int = 0, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._timeout = min(float(timeout), 300.0)
        self._max_tokens = max_tokens
        self._clock = monotonic
        self._sleep = sleep
        self._client = httpx.Client(base_url=self._base_url, transport=transport)

    def close(self) -> None:
        self._client.close()

    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str:
        if context is None:
            raise broker_error("broker_context_missing")
        operations = plan_operations(transcript, self._max_tokens)
        partials: list[str] = []
        for operation in operations[:-1] if len(operations) > 1 else operations:
            partials.append(self.submit(operation, context))
        if len(operations) == 1:
            return partials[0]
        final = operations[-1]
        prefix, _ = final.user_prompt.split("RESUMENES PARCIALES:\n\n", 1)
        final = SummaryOperation(final.kind, final.ordinal, final.system_prompt,
                                 prefix + "RESUMENES PARCIALES:\n\n" + "\n\n---\n\n".join(partials),
                                 final.max_tokens, final.stop)
        return self.submit(final, context)

    def submit(self, operation: SummaryOperation, context: SummaryGatewayContext) -> str:
        key = idempotency_key(context, operation)
        generation: dict[str, object] = {
            "max_tokens": operation.max_tokens,
            "temperature": 0.7,
        }
        if operation.stop:
            generation["stop"] = list(operation.stop)
        body = {
            "workload": "batch-summary",
            "capability": "summarize",
            "messages": [
                {"role": "system", "content": operation.system_prompt},
                {"role": "user", "content": operation.user_prompt},
            ],
            "generation": generation,
            "response": {"kind": "text"},
        }
        deadline = self._clock() + self._timeout
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise broker_error("broker_timeout")
        try:
            response = self._request("POST", "/v1/tasks", key=key, json=body, prefer=True,
                                    timeout=remaining)
        except (httpx.HTTPError, ValueError):
            raise broker_error("broker_transport_error") from None
        if deadline - self._clock() <= 0:
            raise broker_error("broker_timeout")
        if response.status_code == 200:
            return self._validated_result(response)
        if response.status_code not in (201, 202):
            raise broker_error(_problem_code(response))
        task_id = self._location_id(response)
        _validate_task(_json_dict(response), task_id)
        for _ in range(math.ceil(self._timeout)):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise broker_error("broker_timeout")
            self._sleep(min(1.0, remaining))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise broker_error("broker_timeout")
            try:
                polled = self._request("GET", f"/v1/tasks/{task_id}/result", timeout=remaining)
            except httpx.HTTPError:
                raise broker_error("broker_transport_error") from None
            if polled.status_code != 200:
                raise broker_error(_problem_code(polled))
            state = _task_result_status(polled)
            if state == "pending":
                continue
            if state == "succeeded":
                return self._validated_result(polled)
            if state in {"failed", "cancelled", "expired"}:
                raise broker_error(f"broker_task_{state}")
            raise broker_error("broker_protocol_error")
        raise broker_error("broker_timeout")

    def _request(self, method: str, path: str, *, key: str | None = None,
                 json: object = None, prefer: bool = False, timeout: float | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._credential}"}
        if key:
            headers.update({"Idempotency-Key": key, "Content-Type": "application/json"})
        if prefer:
            headers["Prefer"] = "wait=30"
        return self._client.request(method, path, headers=headers, json=json, timeout=timeout or self._timeout)

    @staticmethod
    def _validated_result(response: httpx.Response) -> str:
        payload = _json_dict(response)
        if set(payload) != {"status", "result"} or payload.get("status") != "succeeded":
            raise broker_error("broker_protocol_error")
        result = payload.get("result")
        if not isinstance(result, dict) or set(result) != {"content", "finish_reason", "usage"}:
            raise broker_error("broker_protocol_error")
        text = result.get("content")
        if not isinstance(text, str) or result.get("finish_reason") not in {
            "stop", "length", "content_filter", "unknown"
        } or not _valid_usage(result.get("usage")):
            raise broker_error("broker_protocol_error")
        try:
            return validate_broker_output(text)
        except ValueError:
            raise broker_error("broker_output_invalid") from None

    def _location_id(self, response: httpx.Response) -> str:
        location = response.headers.get("Location")
        if not location:
            raise broker_error("broker_location_invalid")
        parsed = urlparse(location)
        base = urlparse(self._base_url)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise broker_error("broker_location_invalid")
        if parsed.query or parsed.fragment or parsed.params:
            raise broker_error("broker_location_invalid")
        decoded_path = unquote(parsed.path)
        if decoded_path != parsed.path or "\\" in decoded_path:
            raise broker_error("broker_location_invalid")
        resolved = urlparse(urljoin(self._base_url + "/", location))
        parts = resolved.path.split("/")
        task_id = parts[3] if len(parts) == 4 and parts[:3] == ["", "v1", "tasks"] else ""
        if not task_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise broker_error("broker_location_invalid")
        return task_id


def _json_dict(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise broker_error("broker_protocol_error") from None
    if not isinstance(payload, dict):
        raise broker_error("broker_protocol_error")
    return payload


def _valid_usage(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"prompt_tokens", "completion_tokens", "total_tokens"}:
        return False
    return all(isinstance(value[key], int) and not isinstance(value[key], bool) and value[key] >= 0
               for key in value)


def _validate_task(payload: dict, task_id: str) -> None:
    required = {"id", "workload", "capability", "status", "created_at", "updated_at"}
    if set(payload) - required or not required <= set(payload):
        raise broker_error("broker_protocol_error")
    if payload["id"] != task_id or not isinstance(payload["id"], str):
        raise broker_error("broker_protocol_error")
    if any(not isinstance(payload[key], str) for key in ("workload", "capability", "status", "created_at", "updated_at")):
        raise broker_error("broker_protocol_error")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", payload["workload"]) or len(payload["workload"]) > 64:
        raise broker_error("broker_protocol_error")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", payload["capability"]) or len(payload["capability"]) > 64:
        raise broker_error("broker_protocol_error")
    if payload["status"] not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        raise broker_error("broker_protocol_error")
    if any(_parse_datetime(payload[key]) is None for key in ("created_at", "updated_at")):
        raise broker_error("broker_protocol_error")
    if "attempts" in payload and (not isinstance(payload["attempts"], int)
                                   or isinstance(payload["attempts"], bool) or payload["attempts"] < 0):
        raise broker_error("broker_protocol_error")


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _task_result_status(response: httpx.Response) -> str:
    payload = _json_dict(response)
    if set(payload) - {"status", "result", "error"} or not isinstance(payload.get("status"), str):
        raise broker_error("broker_protocol_error")
    state = payload["status"]
    if state == "succeeded":
        # This also validates the complete InferenceResult envelope.
        _validate_terminal_result(payload)
    elif state == "pending":
        if set(payload) != {"status"}:
            raise broker_error("broker_protocol_error")
    elif state in {"failed", "cancelled", "expired"}:
        if set(payload) != {"status", "error"}:
            raise broker_error("broker_protocol_error")
        _validate_classified_error(payload["error"])
    else:
        raise broker_error("broker_protocol_error")
    return state


def _validate_terminal_result(payload: dict) -> None:
    if set(payload) != {"status", "result"} or payload.get("status") != "succeeded":
        raise broker_error("broker_protocol_error")


def _validate_classified_error(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"class", "code", "message"}:
        raise broker_error("broker_protocol_error")
    if not all(isinstance(value[key], str) and value[key] for key in ("class", "code", "message")):
        raise broker_error("broker_protocol_error")
    if value["class"] not in {
        "client_invalid",
        "policy_rejected",
        "backend_rejected",
        "output_invalid",
        "transient_unsent",
        "transient_safe",
        "indeterminate",
    }:
        raise broker_error("broker_protocol_error")


def _problem_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "broker_protocol_error"
    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
        return {"unauthorized": "broker_unauthorized", "invalid_request": "broker_invalid_request"}.get(
            payload["code"], "broker_protocol_error"
        )
    return "broker_protocol_error"
