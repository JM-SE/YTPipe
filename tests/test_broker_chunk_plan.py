from __future__ import annotations

import pytest

from app.services.broker_chunking import (
    BrokerChunkConfig,
    BrokerChunkPlanError,
    build_plan,
    estimated_request_tokens,
    split_transcript,
    validate_plan,
)


def test_small_transcript_keeps_single_shot() -> None:
    config = BrokerChunkConfig()
    assert build_plan("hola" * 100, config) is None


def test_context_and_chunk_size_are_configurable() -> None:
    text = "palabra " * 9_500
    assert build_plan(text, BrokerChunkConfig(enabled=True)) is not None
    assert build_plan(text, BrokerChunkConfig(enabled=True, context_tokens=32_768)) is None
    broker_config = BrokerChunkConfig(enabled=True, max_tokens=2048)
    plan = build_plan(text, broker_config)
    assert plan is not None
    assert plan["config"]["max_tokens"] == 2048
    assert BrokerChunkConfig().max_tokens == 1024


def test_plan_is_bounded_and_fingerprinted() -> None:
    config = BrokerChunkConfig(enabled=True, context_tokens=2048, max_tokens=1, chunk_chars=5000, overlap_chars=100, max_chunks=10)
    text = "a" * 30000
    plan = build_plan(text, config)
    assert plan is not None
    assert len(plan["steps"]) == 8
    assert plan["next_step"] == 0
    assert all("task_id" not in step and "idempotency_key" not in step for step in plan["steps"])
    assert validate_plan(plan, text) is plan


def test_oversized_plan_fails_closed() -> None:
    config = BrokerChunkConfig(enabled=True, context_tokens=2048, max_tokens=1, chunk_chars=5000, overlap_chars=100, max_chunks=2)
    with pytest.raises(BrokerChunkPlanError):
        build_plan("a" * 30000, config)


def test_overlap_split_is_deterministic_and_covers_text() -> None:
    config = BrokerChunkConfig(enabled=True, chunk_chars=100, overlap_chars=10, max_chunks=10)
    text = "a" * 250
    ranges = split_transcript(text, config)
    assert ranges == split_transcript(text, config)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    assert all(previous_end - 10 <= start < previous_end for (_, previous_end), (start, _) in zip(ranges, ranges[1:]))


def test_large_configured_overlap_is_capped_for_small_context() -> None:
    config = BrokerChunkConfig(
        enabled=True,
        context_tokens=2048,
        max_tokens=1,
        chunk_chars=24000,
        overlap_chars=2000,
        max_chunks=4,
    )
    plan = build_plan("a" * 6000, config)
    assert plan is not None
    assert len(plan["steps"]) == 3


def test_estimator_is_conservative_and_validation_rejects_bad_plan() -> None:
    config = BrokerChunkConfig()
    assert estimated_request_tokens(73_000, config) > 16_000
    with pytest.raises(BrokerChunkPlanError):
        validate_plan({"version": 1, "fingerprint": "bad", "steps": [], "next_step": 0}, "text")


def test_plan_rejects_configuration_snapshot_changes() -> None:
    config = BrokerChunkConfig(enabled=True, context_tokens=2048, max_tokens=1, chunk_chars=1000, overlap_chars=100, max_chunks=10)
    text = "a" * 6000
    plan = build_plan(text, config)
    assert plan is not None
    plan["config"]["context_tokens"] = 4096
    with pytest.raises(BrokerChunkPlanError):
        validate_plan(plan, text)


def test_plan_rejects_non_monotonic_ranges() -> None:
    config = BrokerChunkConfig(enabled=True, context_tokens=2048, max_tokens=1, chunk_chars=1000, overlap_chars=100, max_chunks=10)
    plan = build_plan("a" * 6000, config)
    assert plan is not None
    plan["steps"][1]["end"] = plan["steps"][0]["end"]
    with pytest.raises(BrokerChunkPlanError):
        validate_plan(plan, "a" * 6000)
