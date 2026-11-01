"""Anthropic backend.

Uses the official SDK. The story bible is sent as a cached system block: it is
stable across chapters, so prompt caching cuts the cost of the repeated prefix.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from ..config import Config, anthropic_api_key
from .base import GenerationRequest, Provider, ProviderError

_AUTH_HINT = (
    "No Anthropic credentials found. Set ANTHROPIC_API_KEY, run `ant auth login`, "
    "or run `verso config set anthropic_api_key <key>`. "
    "To use a local model instead, run `verso config set provider ollama`."
)


def _looks_like_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "authentication method" in message or "api_key" in message


class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-opus-5-5"

    def __init__(self, model: str | None = None, config: Config | None = None) -> None:
        super().__init__(model)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ProviderError(
                "The Anthropic provider needs the 'anthropic' package. "
                "Install it with: pip install 'verso-cli[anthropic]'"
            ) from exc

        self._anthropic = anthropic
        key = (
            anthropic_api_key(config)
            if config is not None
            else os.environ.get("ANTHROPIC_API_KEY")
        )
        # With no key, let the SDK resolve an `ant auth login` profile.
        self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        kwargs: dict[str, object] = {
            "model": request.model or self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": request.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        try:
            with self._client.messages.stream(**kwargs) as stream:
                yield from stream.text_stream
        except self._anthropic.APIError as exc:
            raise ProviderError(self._describe(exc)) from exc
        except TypeError as exc:
            # The SDK resolves credentials lazily, on the first request.
            if _looks_like_auth_error(exc):
                raise ProviderError(_AUTH_HINT) from exc
            raise

    def _describe(self, exc: Exception) -> str:
        anthropic = self._anthropic
        if isinstance(exc, anthropic.AuthenticationError):
            return (
                "Anthropic authentication failed. Set ANTHROPIC_API_KEY or run "
                "`verso config set anthropic_api_key <key>`."
            )
        if isinstance(exc, anthropic.RateLimitError):
            return "Anthropic rate limit reached. Wait a moment and try again."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Could not reach the Anthropic API. Check your connection."
        if isinstance(exc, anthropic.APIStatusError):
            detail = getattr(exc, "message", str(exc))
            return f"Anthropic API error {exc.status_code}: {detail}"
        return f"Anthropic request failed: {exc}"
