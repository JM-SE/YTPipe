"""Pure eligibility rules for safe new broker submission generations."""

from __future__ import annotations


SAFE_TERMINAL_CLASSES = frozenset({"transient_safe", "transient_unsent"})


def can_advance_submission_epoch(
    *,
    route: str,
    terminal_confirmed: bool,
    task_id: str | None,
    current_task_id: str | None,
    failure_class: str | None,
    attempt_count: int,
    max_attempts: int,
    current_epoch: int,
) -> bool:
    """Check only typed facts; no parsing, persistence, hashing, or I/O."""
    return (
        route == "broker"
        and terminal_confirmed
        and bool(task_id)
        and task_id == current_task_id
        and failure_class in SAFE_TERMINAL_CLASSES
        and attempt_count < max_attempts
        and current_epoch < max_attempts
    )
