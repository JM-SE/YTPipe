import pytest

from app.services.broker_summary import (
    BROKER_VALIDATION_ERROR,
    validate_broker_output,
    validate_broker_partial_output,
)
from app.services.summarization_gateway import SummaryGatewayContext
from test_y00_gateway_offline import gateway, response, task_result


def summary(count, conclusion=False):
    text = "RESUMEN\nTema central.\n\nPUNTOS CLAVE\n" + "\n".join(
        f"• Dato {i}." for i in range(count)
    )
    return text + ("\n\nCONCLUSIÓN\nUna incertidumbre adicional." if conclusion else "")


@pytest.mark.parametrize("count", [1, 3, 4, 7, 8, 12])
@pytest.mark.parametrize("conclusion", [False, True])
def test_broker_accepts_adaptive_summary_through_gateway(count, conclusion):
    expected = summary(count, conclusion)
    result = task_result()
    result["result"]["content"] = expected
    service = gateway(lambda request: response(200, json=result, headers={"Location": "/v1/tasks/t-1"}))
    try:
        assert service.summarize("transcripcion", context=SummaryGatewayContext(9)) == expected
    finally:
        service.close()


@pytest.mark.parametrize("count", [1, 4, 5, 8, 9, 12])
def test_partial_notes_have_no_fixed_bullet_quota(count):
    notes = "\n".join(f"• Hecho {i}." for i in range(count))
    assert validate_broker_partial_output(notes) == notes


@pytest.mark.parametrize("value", [
    summary(0),
    summary(1) + "\nCONCLUSIÓN",
    summary(1, True) + "\nCONCLUSIÓN\nOtra.",
    summary(1).replace("• Dato 0.", "•"),
    summary(1).replace("• Dato 0.", "texto sin bullet"),
    summary(1).replace("• Dato 0.", " • Dato 0."),
    summary(1).replace("Tema central.", ""),
    summary(1).replace("PUNTOS CLAVE", "PUNTOS CLAVE:"),
    summary(1).replace("Tema central.", "<think>razonamiento</think>"),
])
def test_adaptive_summary_preserves_structural_guards(value):
    with pytest.raises(ValueError, match=f"^{BROKER_VALIDATION_ERROR}$"):
        validate_broker_output(value)


@pytest.mark.parametrize("value", ["", "•", "• dato\n • otro", "prosa", "• <think>dato", "• </THINK>"])
def test_partial_notes_preserve_guards(value):
    with pytest.raises(ValueError, match=f"^{BROKER_VALIDATION_ERROR}$"):
        validate_broker_partial_output(value)
