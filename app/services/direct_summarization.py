from __future__ import annotations

from app.core.settings import Settings
from app.services.summarization import SummarizationService


class DirectSummarizationGateway(SummarizationService):
    """The product's only composition: the existing llama.cpp implementation."""


def build_summarization_gateway(settings: Settings) -> DirectSummarizationGateway:
    return DirectSummarizationGateway(settings)
