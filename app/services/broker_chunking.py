"""Deterministic, bounded planning for broker map-reduce summaries."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


PLAN_VERSION = 1
ESTIMATED_PROMPT_OVERHEAD_TOKENS = 700


class BrokerChunkPlanError(ValueError):
    """The transcript cannot be represented by the configured chunk plan."""


@dataclass(frozen=True, slots=True)
class BrokerChunkConfig:
    enabled: bool = False
    context_tokens: int = 16_384
    chunk_chars: int = 24_000
    overlap_chars: int = 2_000
    max_chunks: int = 6
    chars_per_token: float = 4.0
    max_tokens: int = 1_024

    def validate(self) -> None:
        if self.context_tokens < 2_048:
            raise BrokerChunkPlanError("broker chunk context is too small")
        if self.chunk_chars < 1:
            raise BrokerChunkPlanError("broker chunk size must be positive")
        if self.overlap_chars < 0 or self.overlap_chars >= self.chunk_chars:
            raise BrokerChunkPlanError("broker chunk overlap must be smaller than chunk size")
        if self.max_chunks < 1:
            raise BrokerChunkPlanError("broker chunk maximum must be positive")
        if not 1.0 <= self.chars_per_token <= 8.0:
            raise BrokerChunkPlanError("broker chars per token must be between 1 and 8")
        if self.max_tokens < 0:
            raise BrokerChunkPlanError("broker max tokens must not be negative")


def estimated_request_tokens(characters: int, config: BrokerChunkConfig) -> int:
    config.validate()
    return math.ceil(max(0, characters) / config.chars_per_token) + ESTIMATED_PROMPT_OVERHEAD_TOKENS + config.max_tokens


def _chunk_capacity(config: BrokerChunkConfig) -> int:
    available = config.context_tokens - config.max_tokens - ESTIMATED_PROMPT_OVERHEAD_TOKENS
    if available <= 0:
        raise BrokerChunkPlanError("broker context leaves no transcript budget")
    return max(1, math.floor(available * config.chars_per_token))


def _effective_overlap(config: BrokerChunkConfig, capacity: int | None = None) -> int:
    capacity = _chunk_capacity(config) if capacity is None else capacity
    # Keep forward progress bounded even when a small context makes the
    # configured overlap larger than the effective chunk capacity.
    return min(config.overlap_chars, capacity // 2)


def _fingerprint(transcript: str, config: dict) -> str:
    canonical = json.dumps(
        {"version": PLAN_VERSION, "config": config, "transcript": transcript},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _find_boundary(text: str, start: int, target: int) -> int:
    if target >= len(text):
        return len(text)
    floor = start + max(1, (target - start) // 2)
    candidates = [
        text.rfind("\n\n", floor, target),
        text.rfind("\n", floor, target),
        text.rfind(". ", floor, target),
        text.rfind("? ", floor, target),
        text.rfind("! ", floor, target),
    ]
    boundary = max(candidates)
    return boundary + 1 if boundary >= 0 else target


def split_transcript(text: str, config: BrokerChunkConfig) -> list[tuple[int, int]]:
    config.validate()
    capacity = min(config.chunk_chars, _chunk_capacity(config))
    overlap = _effective_overlap(config, capacity)
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = _find_boundary(text, start, min(len(text), start + capacity))
        if end <= start:
            end = min(len(text), start + capacity)
        chunks.append((start, end))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def build_plan(text: str, config: BrokerChunkConfig) -> dict | None:
    """Return a JSON-safe plan, or None when the current single-shot fits."""
    config.validate()
    if estimated_request_tokens(len(text), config) <= config.context_tokens:
        return None
    if not config.enabled:
        raise BrokerChunkPlanError("broker chunking is disabled")
    ranges = split_transcript(text, config)
    if len(ranges) > config.max_chunks:
        raise BrokerChunkPlanError("broker chunk plan exceeds configured maximum")
    config_snapshot = {
        "context_tokens": config.context_tokens,
        "chunk_chars": config.chunk_chars,
        "overlap_chars": config.overlap_chars,
        "max_chunks": config.max_chunks,
        "chars_per_token": float(config.chars_per_token),
        "max_tokens": config.max_tokens,
    }
    return {
        "version": PLAN_VERSION,
        "fingerprint": _fingerprint(text, config_snapshot),
        "config": config_snapshot,
        "next_step": 0,
        "steps": [
            {
                "kind": "partial",
                "ordinal": index,
                "start": start,
                "end": end,
                "status": "pending",
                "output": None,
            }
            for index, (start, end) in enumerate(ranges)
        ]
        + [{"kind": "final", "ordinal": 0, "status": "pending", "output": None}],
    }


def validate_plan(plan: object, transcript: str) -> dict:
    if not isinstance(plan, dict) or set(plan) != {"version", "fingerprint", "config", "next_step", "steps"}:
        raise BrokerChunkPlanError("broker chunk plan version is invalid")
    if type(plan["version"]) is not int or plan["version"] != PLAN_VERSION:
        raise BrokerChunkPlanError("broker chunk plan version is invalid")
    if type(plan["fingerprint"]) is not str or len(plan["fingerprint"]) != 64:
        raise BrokerChunkPlanError("broker chunk plan fingerprint is invalid")
    steps = plan.get("steps")
    next_step = plan.get("next_step")
    config = plan.get("config")
    if not isinstance(config, dict) or set(config) != {
        "context_tokens", "chunk_chars", "overlap_chars", "max_chunks", "chars_per_token", "max_tokens"
    }:
        raise BrokerChunkPlanError("broker chunk plan configuration is invalid")
    try:
        if (
            any(isinstance(config[key], bool) for key in ("context_tokens", "chunk_chars", "overlap_chars", "max_chunks", "max_tokens"))
            or any(type(config[key]) is not int for key in ("context_tokens", "chunk_chars", "overlap_chars", "max_chunks", "max_tokens"))
            or isinstance(config["chars_per_token"], bool)
            or type(config["chars_per_token"]) is not float
        ):
            raise BrokerChunkPlanError("broker chunk plan configuration types are invalid")
        if plan.get("fingerprint") != _fingerprint(transcript, config):
            raise BrokerChunkPlanError("broker chunk plan snapshot changed")
        active_config = BrokerChunkConfig(
            enabled=True,
            context_tokens=int(config["context_tokens"]),
            chunk_chars=int(config["chunk_chars"]),
            overlap_chars=int(config["overlap_chars"]),
            max_chunks=int(config["max_chunks"]),
            chars_per_token=float(config["chars_per_token"]),
            max_tokens=int(config["max_tokens"]),
        )
        active_config.validate()
    except (KeyError, TypeError, ValueError, BrokerChunkPlanError) as exc:
        raise BrokerChunkPlanError("broker chunk plan configuration is invalid") from exc
    if not isinstance(steps, list) or not steps or type(next_step) is not int or not 0 <= next_step < len(steps):
        raise BrokerChunkPlanError("broker chunk plan state is invalid")
    partial_steps = steps[:-1]
    if not partial_steps or len(partial_steps) > active_config.max_chunks:
        raise BrokerChunkPlanError("broker chunk plan exceeds configured maximum")
    capacity = min(active_config.chunk_chars, _chunk_capacity(active_config))
    overlap = _effective_overlap(active_config, capacity)
    previous_end = 0
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("status") not in {"pending", "completed"}:
            raise BrokerChunkPlanError("broker chunk plan step is invalid")
        if step.get("kind") == "partial":
            if set(step) != {"kind", "ordinal", "start", "end", "status", "output"}:
                raise BrokerChunkPlanError("broker chunk plan partial schema is invalid")
            if (
                type(step.get("ordinal")) is not int
                or step.get("ordinal") != index
                or type(step.get("start")) is not int
                or type(step.get("end")) is not int
                or not 0 <= step["start"] < step["end"] <= len(transcript)
                or step["end"] - step["start"] > capacity
                or (index == 0 and step["start"] != 0)
                or (index > 0 and not 0 <= previous_end - step["start"] <= overlap)
                or (index > 0 and step["end"] <= previous_end)
            ):
                raise BrokerChunkPlanError("broker chunk plan partial is invalid")
            previous_end = step["end"]
        elif step.get("kind") == "final":
            if set(step) != {"kind", "ordinal", "status", "output"}:
                raise BrokerChunkPlanError("broker chunk plan final schema is invalid")
            if index != len(steps) - 1 or step.get("ordinal") != 0:
                raise BrokerChunkPlanError("broker chunk plan final is invalid")
        else:
            raise BrokerChunkPlanError("broker chunk plan kind is invalid")
        if step.get("status") == "completed" and (not isinstance(step.get("output"), str) or not step["output"]):
            raise BrokerChunkPlanError("broker chunk plan output is invalid")
        if step.get("status") == "pending" and step.get("output") is not None:
            raise BrokerChunkPlanError("broker chunk plan pending output is invalid")
    if previous_end != len(transcript) or steps[-1]["status"] != "pending":
        raise BrokerChunkPlanError("broker chunk plan coverage is invalid")
    if any(step["status"] != "completed" for step in steps[:next_step]):
        raise BrokerChunkPlanError("broker chunk plan progress is invalid")
    if any(step["status"] == "completed" for step in steps[next_step:]):
        raise BrokerChunkPlanError("broker chunk plan progress is invalid")
    return plan


def config_from_plan(plan: dict) -> BrokerChunkConfig:
    config = plan["config"]
    return BrokerChunkConfig(
        enabled=True,
        context_tokens=int(config["context_tokens"]),
        chunk_chars=int(config["chunk_chars"]),
        overlap_chars=int(config["overlap_chars"]),
        max_chunks=int(config["max_chunks"]),
        chars_per_token=float(config["chars_per_token"]),
        max_tokens=int(config["max_tokens"]),
    )
