from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import httpx

from app.services.broker_errors import BrokerSummarizationError, broker_error
from app.services.broker_summary import validate_broker_output
from app.services.broker_profile import BrokerRequestProfile
from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation, idempotency_key
from app.services.summarization_planner import plan_operations


BrokerOperation = SummaryOperation


@dataclass(frozen=True, slots=True)
class BrokerResult:
    content: str
    finish_reason: str
    usage: dict[str, int]


@dataclass(frozen=True, slots=True)
class AcceptedTask:
    """Opaque accepted-task handle returned by a POST-only submission.

    The caller (persistence owner) stores ``task_id`` and ``idempotency_key``
    durably BEFORE issuing any result GET. The adapter never polls, never
    performs session I/O, and never owns durable transitions.
    """

    task_id: str
    idempotency_key: str


class BrokerTaskClient:
    """Reusable B00 task protocol client for non-pipeline callers."""

    def __init__(self, *, base_url: str, credential: str, transport: httpx.BaseTransport,
                 timeout: float, profile: BrokerRequestProfile | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._gateway = BrokerSummarizationGateway(
            base_url=base_url, credential=credential, transport=transport,
            timeout=timeout, profile=profile, monotonic=monotonic, sleep=sleep,
        )

    @classmethod
    def from_client(cls, client: httpx.Client, *, timeout: float,
                    profile: BrokerRequestProfile | None = None,
                    monotonic: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> "BrokerTaskClient":
        instance = cls.__new__(cls)
        gateway = BrokerSummarizationGateway.__new__(BrokerSummarizationGateway)
        gateway._base_url = str(client.base_url).rstrip("/")
        gateway._credential = ""
        gateway._timeout = min(float(timeout), 360.0)
        gateway._max_tokens = 0
        gateway._profile = profile
        gateway._clock = monotonic
        gateway._sleep = sleep
        gateway._client = client
        instance._gateway = gateway
        return instance

    def close(self) -> None:
        self._gateway.close()

    def submit(self, operation: BrokerOperation, idempotency_key_value: str) -> str:
        return self._gateway._submit_with_key(operation, idempotency_key_value)

    def submit_result(self, operation: BrokerOperation, idempotency_key_value: str) -> BrokerResult:
        return self._gateway._submit_result_with_key(operation, idempotency_key_value)

    def submit_task(self, operation: BrokerOperation, idempotency_key_value: str) -> AcceptedTask:
        """POST only for persistence-owned pipeline submissions.

        The pipeline deliberately uses the non-blocking broker response so the
        accepted task correlation can be committed before any result GET.
        """
        return self._gateway.submit_task(operation, idempotency_key_value)

    def poll_result(
        self,
        task_id: str,
        idempotency_key_value: str,
        *,
        timeout: float | None = None,
    ) -> BrokerResult | None:
        """Single GET result poll; None when the task is still pending."""
        return self._gateway.poll_result(task_id, idempotency_key_value, timeout=timeout)

    def reconcile_result(self, task_id: str, *, idempotency_key: str | None = None) -> BrokerResult | None:
        return self._gateway.reconcile_result(task_id, idempotency_key=idempotency_key)

    @property
    def timeout_seconds(self) -> float:
        return self._gateway._timeout


class BrokerSummarizationGateway:
    """Dormant B00 client. Construction requires an explicitly injected transport."""

    def __init__(self, *, base_url: str, credential: str, transport: httpx.BaseTransport,
                 timeout: float, max_tokens: int = 0, profile: BrokerRequestProfile | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._timeout = min(float(timeout), 360.0)
        self._max_tokens = max_tokens
        self._profile = profile
        self._clock = monotonic
        self._sleep = sleep
        self._client = httpx.Client(base_url=self._base_url, transport=transport, follow_redirects=False, trust_env=False)

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
        return self._submit_with_key(operation, key)

    def _submit_with_key(self, operation: SummaryOperation, key: str) -> str:
        result = self._submit_result_with_key(operation, key)
        try:
            return validate_broker_output(result.content)
        except ValueError:
            raise broker_error("broker_output_invalid") from None

    def _submit_result_with_key(self, operation: SummaryOperation, key: str) -> BrokerResult:
        """Blocking submit-then-poll convenience (probe/dormant gateway path).

        The pipeline persistence owner must NOT use this fused method: it uses
        ``submit_task`` + ``poll_result`` so the task handle is durably stored
        before the first result GET.
        """
        deadline = self._clock() + self._timeout
        accepted = self._submit_task_response(operation, key, prefer=True)
        if isinstance(accepted, BrokerResult):
            return accepted
        task_id = accepted.task_id
        for _ in range(math.ceil(self._timeout)):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise broker_error("broker_timeout", task_id=task_id, idempotency_key=key)
            self._sleep(min(1.0, remaining))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise broker_error("broker_timeout", task_id=task_id, idempotency_key=key)
            polled = self.poll_result(task_id, key, timeout=remaining)
            if polled is None:
                continue
            return polled
        raise broker_error("broker_timeout", task_id=task_id, idempotency_key=key)

    def submit_task(self, operation: SummaryOperation, key: str) -> AcceptedTask:
        """POST only for the persistence-owned asynchronous protocol.

        No ``Prefer`` header is sent: the broker returns ``201`` with a
        validated task envelope and ``Location``. The persistence owner stores
        that correlation before issuing a result GET.
        """
        accepted = self._submit_task_response(operation, key, prefer=False)
        if not isinstance(accepted, AcceptedTask):
            raise broker_error("broker_protocol_error", idempotency_key=key)
        return accepted

    def _submit_task_response(
        self,
        operation: SummaryOperation,
        key: str,
        *,
        prefer: bool,
    ) -> BrokerResult | AcceptedTask:
        """Parse one POST response for either blocking or durable callers.

        The blocking path may consume a terminal ``200``; it still requires a
        valid ``Location`` first so terminal errors retain task correlation.
        """
        profile = self._profile
        generation: dict[str, object] = {
            "max_tokens": operation.max_tokens,
            "temperature": profile.temperature if profile is not None else 0.7,
        }
        if operation.stop:
            generation["stop"] = list(operation.stop)
        body = {
            "workload": profile.workload if profile is not None else "batch-summary",
            "capability": profile.capability if profile is not None else "summarize",
            "messages": [
                {"role": "system", "content": operation.system_prompt},
                {"role": "user", "content": operation.user_prompt},
            ],
            "generation": generation,
            "response": {"kind": profile.response_kind if profile is not None else "text"},
        }
        deadline = self._clock() + self._timeout
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise broker_error("broker_timeout", idempotency_key=key)
        try:
            response = self._request("POST", "/v1/tasks", key=key, json=body, prefer=prefer,
                                    timeout=remaining)
        except (httpx.HTTPError, ValueError):
            raise broker_error("broker_transport_error", idempotency_key=key) from None
        if deadline - self._clock() <= 0:
            raise broker_error("broker_timeout", idempotency_key=key)
        if response.status_code == 200:
            task_id = self._location_id(response, idempotency_key=key)
            if not prefer:
                # The persistence-owned submission is asynchronous-only. A
                # terminal 200 here is a broker contract mismatch, not a
                # business result the coordinator may consume. Keep the
                # validated correlation for GET-only operator resolution.
                raise broker_error(
                    "broker_protocol_error",
                    task_id=task_id,
                    idempotency_key=key,
                )
            try:
                state = _task_result_status(response)
            except BrokerSummarizationError as exc:
                exc.task_id = task_id
                exc.idempotency_key = key
                raise
            if state == "succeeded":
                try:
                    return self._validated_result(response)
                except BrokerSummarizationError as exc:
                    exc.task_id = task_id
                    exc.idempotency_key = key
                    raise
            if state in {"failed", "cancelled", "expired"}:
                raise _terminal_task_error(response, state, task_id=task_id, idempotency_key=key)
            raise broker_error("broker_protocol_error", task_id=task_id, idempotency_key=key)
        if response.status_code not in (201, 202):
            raise broker_error(_problem_code(response), idempotency_key=key)
        task_id = self._location_id(response, idempotency_key=key)
        try:
            _validate_task(_json_dict(response), task_id)
        except BrokerSummarizationError as exc:
            exc.task_id = task_id
            exc.idempotency_key = key
            raise
        return AcceptedTask(task_id=task_id, idempotency_key=key)

    def poll_result(self, task_id: str, key: str, *, timeout: float | None = None) -> BrokerResult | None:
        """Single GET result poll; None while the task is still pending."""
        try:
            polled = self._request("GET", f"/v1/tasks/{task_id}/result", timeout=timeout or self._timeout)
        except httpx.HTTPError:
            raise broker_error("broker_transport_error", task_id=task_id, idempotency_key=key) from None
        if polled.status_code != 200:
            raise broker_error(_problem_code(polled), task_id=task_id, idempotency_key=key)
        try:
            state = _task_result_status(polled)
        except BrokerSummarizationError as exc:
            exc.task_id = task_id
            exc.idempotency_key = key
            raise
        if state == "pending":
            return None
        if state == "succeeded":
            try:
                return self._validated_result(polled)
            except BrokerSummarizationError as exc:
                exc.task_id = task_id
                exc.idempotency_key = key
                raise
        if state in {"failed", "cancelled", "expired"}:
            raise _terminal_task_error(polled, state, task_id=task_id, idempotency_key=key)
        raise broker_error("broker_protocol_error", task_id=task_id, idempotency_key=key)

    def _request(self, method: str, path: str, *, key: str | None = None,
                 json: object = None, prefer: bool = False, timeout: float | None = None) -> httpx.Response:
        headers = {} if not self._credential else {"Authorization": f"Bearer {self._credential}"}
        if key:
            headers.update({"Idempotency-Key": key, "Content-Type": "application/json"})
        if prefer:
            headers["Prefer"] = "wait=30"
        return self._client.request(method, path, headers=headers, json=json, timeout=timeout or self._timeout)

    def reconcile_result(self, task_id: str, *, idempotency_key: str | None = None) -> BrokerResult | None:
        """Read an existing task result without submitting or replaying work."""
        try:
            response = self._request("GET", f"/v1/tasks/{task_id}/result")
        except httpx.HTTPError:
            raise broker_error("broker_transport_error", task_id=task_id, idempotency_key=idempotency_key) from None
        if response.status_code != 200:
            raise broker_error(_problem_code(response), task_id=task_id, idempotency_key=idempotency_key)
        try:
            state = _task_result_status(response)
        except BrokerSummarizationError as exc:
            exc.task_id = task_id
            exc.idempotency_key = idempotency_key
            raise
        if state == "pending":
            return None
        if state == "succeeded":
            try:
                return self._validated_result(response)
            except BrokerSummarizationError as exc:
                exc.task_id = task_id
                exc.idempotency_key = idempotency_key
                raise
        raise _terminal_task_error(response, state, task_id=task_id, idempotency_key=idempotency_key)

    @staticmethod
    def _validated_result(response: httpx.Response) -> BrokerResult:
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
        usage = result["usage"]
        return BrokerResult(text, result["finish_reason"], usage)

    def _location_id(self, response: httpx.Response, *, idempotency_key: str | None = None) -> str:
        location = response.headers.get("Location")
        if not location:
            raise broker_error("broker_location_invalid", idempotency_key=idempotency_key)
        parsed = urlparse(location)
        base = urlparse(self._base_url)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise broker_error("broker_location_invalid", idempotency_key=idempotency_key)
        if parsed.query or parsed.fragment or parsed.params:
            raise broker_error("broker_location_invalid", idempotency_key=idempotency_key)
        decoded_path = unquote(parsed.path)
        if decoded_path != parsed.path or "\\" in decoded_path:
            raise broker_error("broker_location_invalid", idempotency_key=idempotency_key)
        resolved = urlparse(urljoin(self._base_url + "/", location))
        parts = resolved.path.split("/")
        task_id = parts[3] if len(parts) == 4 and parts[:3] == ["", "v1", "tasks"] else ""
        if not task_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise broker_error("broker_location_invalid", idempotency_key=idempotency_key)
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
    allowed = required | {"attempts"}
    if set(payload) - allowed or not required <= set(payload):
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
    elif state == "failed":
        if set(payload) != {"status", "error"}:
            raise broker_error("broker_protocol_error")
        _validate_classified_error(payload["error"])
    elif state in {"cancelled", "expired"}:
        if set(payload) != {"status"}:
            raise broker_error("broker_protocol_error")
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
    # Preserve well-formed future classes so YTPipe can quarantine them
    # conservatively instead of incorrectly terminalizing a new broker state.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value["class"]):
        raise broker_error("broker_protocol_error")


def _terminal_task_error(
    response: httpx.Response,
    state: str,
    *,
    task_id: str | None = None,
    idempotency_key: str | None = None,
) -> Exception:
    payload = _json_dict(response)
    error = payload.get("error")
    if state == "failed" and isinstance(error, dict):
        failure_class = error.get("class")
        broker_code = error.get("code")
        if isinstance(failure_class, str) and isinstance(broker_code, str):
            return broker_error(
                f"broker_task_{state}",
                failure_class=failure_class,
                broker_code=broker_code,
                task_id=task_id,
                idempotency_key=idempotency_key,
            )
    return broker_error(
        f"broker_task_{state}",
        failure_class="terminal",
        broker_code=f"task_{state}",
        task_id=task_id,
        idempotency_key=idempotency_key,
    )
def _problem_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "broker_protocol_error"
    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
        return {
            "unauthorized": "broker_unauthorized",
            "invalid_request": "broker_invalid_request",
            "unprocessable": "broker_invalid_request",
        }.get(
            payload["code"], "broker_protocol_error"
        )
    return "broker_protocol_error"
