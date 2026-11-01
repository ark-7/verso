"""OpenAI backend (Chat Completions, streaming)."""

from __future__ import annotations

import os
from collections.abc import Iterator

from ..config import Config
from .base import GenerationRequest, Provider, ProviderError


class OpenAIProvider(Provider):
    name = "openai"
    default_model = "gpt-4o-mini"

    def __init__(self, model: str | None = None, config: Config | None = None) -> None:
        super().__init__(model)
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ProviderError(
                "The OpenAI provider needs the 'openai' package. "
                "Install it with: pip install 'verso-cli[openai]'"
            ) from exc

        self._openai = openai
        key = os.environ.get("OPENAI_API_KEY") or (
            config.openai_api_key if config else None
        )
        if not key:
            raise ProviderError(
                "No OpenAI API key found. Set OPENAI_API_KEY or run "
                "`verso config set openai_api_key <key>`."
            )
        self._client = openai.OpenAI(api_key=key)

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        try:
            stream = self._client.chat.completions.create(
                model=request.model or self.model,
                messages=messages,
                max_completion_tokens=request.max_tokens,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except self._openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI request failed: {exc}") from exc
