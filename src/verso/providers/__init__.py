"""Provider registry.

Import the concrete backend lazily so a user on Ollama isn't forced to install
both cloud SDKs.
"""

from __future__ import annotations

from ..config import PROVIDERS, load_config
from .base import GenerationRequest, Provider, ProviderError


def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    config = load_config()
    name = (name or config.provider).strip().lower()

    if name == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(model=model or config.model, config=config)
    if name == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider(model=model or config.model, config=config)
    if name == "ollama":
        from .ollama import OllamaProvider

        # `ollama_model` is written by the local-vs-cloud prompt. Keeping it
        # separate stops an Ollama model name leaking into a cloud provider.
        return OllamaProvider(
            model=model or config.ollama_model or config.model, config=config
        )
    raise ProviderError(
        f"Unknown provider {name!r}. Choose one of: {', '.join(PROVIDERS)}."
    )


def provider_status() -> list[tuple[str, str, bool]]:
    """(provider, default model, SDK installed) for `verso providers`."""
    installed = {
        "anthropic": _module_available("anthropic"),
        "openai": _module_available("openai"),
        "ollama": True,  # plain HTTP, always available
    }
    defaults = {
        "anthropic": "claude-opus-5-5",
        "openai": "gpt-4o-mini",
        "ollama": "llama3.1",
    }
    return [(name, defaults[name], installed[name]) for name in PROVIDERS]


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


__all__ = [
    "GenerationRequest",
    "Provider",
    "ProviderError",
    "get_provider",
    "provider_status",
]
