import pytest

from app.services.broker_safe_retry import can_advance_submission_epoch


def test_only_confirmed_safe_terminal_task_with_budget_can_advance() -> None:
    assert can_advance_submission_epoch(
        route="broker",
        terminal_confirmed=True,
        task_id="task-1",
        current_task_id="task-1",
        failure_class="transient_safe",
        attempt_count=1,
        max_attempts=3,
        current_epoch=0,
    )
    assert can_advance_submission_epoch(
        route="broker",
        terminal_confirmed=True,
        task_id="task-1",
        current_task_id="task-1",
        failure_class="transient_unsent",
        attempt_count=2,
        max_attempts=3,
        current_epoch=1,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"route": "direct"},
        {"terminal_confirmed": False},
        {"task_id": None},
        {"failure_class": "indeterminate"},
        {"failure_class": "backend_rejected"},
        {"attempt_count": 3},
        {"current_epoch": 3},
    ],
)
def test_excluded_facts_never_advance(kwargs: dict[str, object]) -> None:
    facts: dict[str, object] = {
        "route": "broker",
        "terminal_confirmed": True,
        "task_id": "task-1",
        "current_task_id": "task-1",
        "failure_class": "transient_safe",
        "attempt_count": 1,
        "max_attempts": 3,
        "current_epoch": 0,
    }
    facts.update(kwargs)
    assert not can_advance_submission_epoch(**facts)  # type: ignore[arg-type]
