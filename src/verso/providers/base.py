"""The provider interface every LLM backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


class ProviderError(Exception):
    """A user-facing provider failure (missing key, network, rate limit, ...)."""


@dataclass(slots=True)
class GenerationRequest:
    prompt: str
    system: str = ""
    model: str | None = None
    max_tokens: int = 8192


class Provider(ABC):
    name: str = ""
    default_model: str = ""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or self.default_model

    @abstractmethod
    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Yield text chunks as they are generated."""

    def complete(self, request: GenerationRequest) -> str:
        return "".join(self.stream(request))
