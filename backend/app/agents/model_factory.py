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
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from app.agents.settings import LLMSettings

logger = logging.getLogger(__name__)


def build_model(settings: LLMSettings) -> Model:
    """Builds and configures an Agents SDK model instance based on LLMSettings.

    Disables SDK tracing globally unless settings.agents_tracing is True.
    Uses OpenAI Responses API model when api_mode is 'responses',
    otherwise uses OpenAIChatCompletionsModel with AsyncOpenAI client.
    """
    # Global SDK tracing control
    set_tracing_disabled(not settings.agents_tracing)

    api_key_str = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key is not None
        else "MISSING_KEY"
    )

    client_kwargs: dict[str, str | None] = {"api_key": api_key_str}
    if settings.openai_base_url:
        client_kwargs["base_url"] = settings.openai_base_url

    async_client = AsyncOpenAI(**client_kwargs)  # type: ignore[arg-type]

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
