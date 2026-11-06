"""Factory for creating OpenAI Agents SDK models based on configuration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents import (
    Model,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    set_tracing_disabled,
)

from app.agents.client_factory import create_async_client

if TYPE_CHECKING:
    from app.agents.settings import LLMSettings

logger = logging.getLogger(__name__)


def build_model(settings: LLMSettings) -> Model:
    """Builds and configures an Agents SDK model instance based on LLMSettings.

    Disables SDK tracing globally (tracing forced off).
    When OPENAI_BASE_URL is set, LLM_API_MODE defaults to chat_completions
    and OpenAIChatCompletionsModel is used with the AsyncOpenAI client.
    """
    # Global SDK tracing control: force off
    set_tracing_disabled(True)

    async_client = create_async_client(settings)

    if settings.llm_api_mode == "responses" and not settings.openai_base_url:
        logger.debug("Building OpenAIResponsesModel with model=%s", settings.llm_model)
        return OpenAIResponsesModel(
            model=settings.llm_model,
            openai_client=async_client,
        )

    logger.debug(
        "Building OpenAIChatCompletionsModel with model=%s, base_url=%s",
        settings.llm_model,
        settings.openai_base_url,
    )
    return OpenAIChatCompletionsModel(
        model=settings.llm_model,
        openai_client=async_client,
    )
