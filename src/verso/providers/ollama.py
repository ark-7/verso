"""Ollama backend.

Talks to the local HTTP API directly with httpx, so no extra SDK is needed.
Cloud models are proxied by the local daemon after ``ollama signin`` and are
addressed with a ``:cloud`` suffix (for example ``gemma4:cloud``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from ..config import Config
from .base import GenerationRequest, Provider, ProviderError

_READ_TIMEOUT = 600.0
CLOUD_TAGS_URL = "https://ollama.com/api/tags"


@dataclass(frozen=True, slots=True)
class OllamaModel:
    """One model option discovered from the local server or the cloud."""

    name: str
    cloud: bool
    size: int = 0
    parameter_size: str = ""
    capabilities: tuple[str, ...] = ()


def _fetch_tags(url: str, *, timeout: float) -> list[dict] | None:
    """Return the ``models`` array, or None if the endpoint was unreachable."""
    headers = {}
    key = os.environ.get("OLLAMA_API_KEY")
    if key and url.startswith("https://ollama.com"):
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            data = client.get(url).json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return [
        entry
        for entry in data.get("models", [])
        if isinstance(entry, dict) and entry.get("name")
    ]


def _cloud_model_name(name: str) -> str:
    """``gemma4:31b`` -> ``gemma4:cloud``; already-cloud names pass through."""
    return f"{name.split(':', 1)[0]}:cloud"


def _is_cloud_model(name: str) -> bool:
    tag = name.rsplit(":", 1)[-1].lower()
    return tag == "cloud" or tag.endswith("-cloud")


def list_local_models(
    host: str = "http://127.0.0.1:11434",
) -> list[OllamaModel] | None:
    """Installed models, or None if the configured server is unreachable."""
    entries = _fetch_tags(f"{host.rstrip('/')}/api/tags", timeout=5.0)
    if entries is None:
        return None
    models = []
    for entry in entries:
        details = entry.get("details")
        details = details if isinstance(details, dict) else {}
        models.append(
            OllamaModel(
                name=entry["name"],
                cloud=False,
                size=int(entry.get("size") or 0),
                parameter_size=str(details.get("parameter_size") or ""),
                capabilities=tuple(entry.get("capabilities") or ()),
            )
        )
    models.sort(key=lambda model: model.name.casefold())
    return models


def list_cloud_models(*, timeout: float = 8.0) -> list[OllamaModel]:
    """Cloud models offered by ollama.com; empty if the catalog is unreachable."""
    entries = _fetch_tags(CLOUD_TAGS_URL, timeout=timeout)
    if entries is None:
        return []
    seen: set[str] = set()
    models = []
    for entry in entries:
        capabilities = tuple(entry.get("capabilities") or ())
        if capabilities and "completion" not in capabilities:
            continue
        name = _cloud_model_name(entry["name"])
        if name in seen:
            continue
        seen.add(name)
        models.append(
            OllamaModel(
                name=name,
                cloud=True,
                size=int(entry.get("size") or 0),
                capabilities=capabilities,
            )
        )
    models.sort(key=lambda model: model.name.casefold())
    return models


def _with_hints(message: str, detail: str) -> str:
    lowered = detail.lower()
    if "more system memory" in lowered:
        message += (
            " Not enough free RAM to load the model. Close other apps, or pull "
            "a smaller model and select it with `--model <name>`."
        )
    if any(
        needle in lowered
        for needle in (
            "sign in",
            "signin",
            "not signed",
            "unauthorized",
            "authentication",
        )
    ):
        message += (
            " Cloud models need `ollama signin` first (or set OLLAMA_API_KEY when "
            "calling ollama.com directly)."
        )
    return message


def _describe_http_error(status_code: int, text: str) -> str:
    return _with_hints(f"Ollama returned {status_code}: {text[:300]}", text)


class OllamaProvider(Provider):
    name = "ollama"
    default_model = "llama3.1"

    def __init__(
        self,
        model: str | None = None,
        config: Config | None = None,
        *,
        read_timeout: float = _READ_TIMEOUT,
    ) -> None:
        super().__init__(model)
        host = config.ollama_host if config else "http://127.0.0.1:11434"
        self.host = host.rstrip("/")
        self._read_timeout = read_timeout
        self._explicit_model = model is not None
        if not self._explicit_model:
            # Respect whatever the user has actually pulled locally.
            self.model = self._local_default()

    def _installed_models(self) -> list[str] | None:
        """Models on the local server, or None if it could not be reached."""
        entries = _fetch_tags(f"{self.host}/api/tags", timeout=5.0)
        if entries is None:
            return None
        return [entry["name"] for entry in entries]

    def _local_default(self) -> str:
        models = self._installed_models()
        return models[0] if models else self.default_model

    def _resolve_model(self) -> str:
        if _is_cloud_model(self.model):
            # Cloud models are not in the local tags list; the daemon proxies them.
            return self.model
        installed = self._installed_models()
        if installed is None:
            # Let the request itself report the connection failure.
            return self.model
        if self.model in installed:
            return self.model
        if not self._explicit_model and installed:
            return installed[0]
        available = ", ".join(installed) if installed else "none installed"
        raise ProviderError(
            f"Ollama model {self.model!r} is not installed (available: {available}). "
            f"Run `ollama pull {self.model}` or pass `--model <name>`."
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": request.model or self._resolve_model(),
            "messages": messages,
            "stream": True,
            "options": {
                "num_predict": request.max_tokens,
                # Local models need a nudge toward prose variety.
                "temperature": 0.8,
            },
        }
        timeout = httpx.Timeout(
            connect=10.0, read=self._read_timeout, write=30.0, pool=10.0
        )

        try:
            with httpx.stream(
                "POST", f"{self.host}/api/chat", json=payload, timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise ProviderError(
                        _describe_http_error(response.status_code, response.text)
                    )
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if error := data.get("error"):
                        raise ProviderError(
                            _with_hints(f"Ollama error: {error}", str(error))
                        )
                    content = data.get("message", {}).get("content")
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Could not reach Ollama at {self.host}. Is `ollama serve` running? "
                f"({exc})"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Ollama returned malformed JSON: {exc}") from exc
