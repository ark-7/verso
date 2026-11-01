"""Provider health checks.

By default these checks are free: they verify SDKs, credentials, connectivity,
and model availability. ``probe=True`` additionally sends a minimal generation
request to confirm inference works and to read rate-limit headers.
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field

import httpx

from .config import PROVIDERS, Config, anthropic_api_key, openai_api_key
from .providers.anthropic import AnthropicProvider
from .providers.base import GenerationRequest, ProviderError
from .providers.ollama import (
    OllamaProvider,
    _is_cloud_model,
    list_cloud_models,
    list_local_models,
)
from .providers.openai import OpenAIProvider

_TIMEOUT = 15.0

_NO_ANTHROPIC = (
    "Set ANTHROPIC_API_KEY, run `ant auth login`, or use "
    "`verso config set anthropic_api_key <key>`."
)
_NO_OPENAI = (
    "Set OPENAI_API_KEY or run `verso config set openai_api_key <key>`."
)

_ANTHROPIC_LIMITS = (
    (
        "requests",
        "anthropic-ratelimit-requests-limit",
        "anthropic-ratelimit-requests-remaining",
    ),
    (
        "tokens",
        "anthropic-ratelimit-tokens-limit",
        "anthropic-ratelimit-tokens-remaining",
    ),
)
_OPENAI_LIMITS = (
    ("requests", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests"),
    ("tokens", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens"),
)


@dataclass(slots=True)
class ProviderHealth:
    name: str
    status: str
    model: str = ""
    detail: str = ""
    latency_ms: int | None = None
    limits: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status in {"ready", "degraded"}


def _available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _limit_summary(headers, pairs) -> dict[str, str]:
    limits = {}
    for label, limit_key, remaining_key in pairs:
        limit = headers.get(limit_key)
        if not limit:
            continue
        remaining = headers.get(remaining_key)
        limits[label] = f"{remaining or '?'} / {limit}"
    return limits


def _model_available(model: str, models: list[str]) -> bool:
    """Match exact ids and common date/version suffixed variants."""
    return any(
        candidate == model
        or candidate.startswith(f"{model}-")
        or candidate.startswith(f"{model}@")
        or model.startswith(f"{candidate}-")
        for candidate in models
    )


def _available_models_detail(model: str, models: list[str]) -> str:
    shown = ", ".join(models[:8])
    if len(models) > 8:
        shown += f", … (+{len(models) - 8})"
    return f"{model!r} not listed; available: {shown}"


def _check_anthropic(config: Config, *, probe: bool) -> ProviderHealth:
    model = config.model or AnthropicProvider.default_model
    if not _available("anthropic"):
        return ProviderHealth(
            "anthropic",
            "not installed",
            model,
            "Install it with: pip install 'verso-cli[anthropic]'",
        )

    import anthropic

    key = anthropic_api_key(config)
    try:
        options = {"timeout": _TIMEOUT, "max_retries": 0}
        client = (
            anthropic.Anthropic(api_key=key, **options)
            if key
            else anthropic.Anthropic(**options)
        )
        models = [entry.id for entry in client.models.list(limit=100).data]
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            return ProviderHealth(
                "anthropic", "no credentials", model, _NO_ANTHROPIC
            )
        return ProviderHealth("anthropic", "error", model, str(exc))
    except anthropic.AuthenticationError:
        return ProviderHealth("anthropic", "auth failed", model, _NO_ANTHROPIC)
    except anthropic.APIConnectionError as exc:
        return ProviderHealth(
            "anthropic", "unreachable", model, f"Could not reach the API: {exc}"
        )
    except anthropic.APIStatusError as exc:
        detail = getattr(exc, "message", str(exc))
        return ProviderHealth(
            "anthropic", "error", model, f"HTTP {exc.status_code}: {detail}"
        )
    except anthropic.APIError as exc:
        return ProviderHealth("anthropic", "error", model, str(exc))

    status = "ready"
    details = [f"{len(models)} models available"]
    if models and not _model_available(model, models):
        status = "degraded"
        details.append(_available_models_detail(model, models))

    latency = None
    limits: dict[str, str] = {}
    if probe:
        start = time.perf_counter()
        try:
            raw = client.messages.with_raw_response.create(
                model=model,
                max_tokens=4,
                messages=[{"role": "user", "content": "Reply with OK."}],
            )
            headers = raw.headers
            raw.parse()
        except TypeError as exc:
            if "authentication" in str(exc).lower():
                return ProviderHealth(
                    "anthropic", "no credentials", model, _NO_ANTHROPIC
                )
            return ProviderHealth("anthropic", "error", model, str(exc))
        except anthropic.RateLimitError:
            return ProviderHealth(
                "anthropic",
                "rate limited",
                model,
                "Credentials are valid, but the probe was rate limited.",
            )
        except anthropic.AuthenticationError:
            return ProviderHealth("anthropic", "auth failed", model, _NO_ANTHROPIC)
        except anthropic.APIConnectionError as exc:
            return ProviderHealth("anthropic", "unreachable", model, str(exc))
        except anthropic.APIStatusError as exc:
            detail = getattr(exc, "message", str(exc))
            return ProviderHealth(
                "anthropic",
                "error",
                model,
                f"Probe failed: HTTP {exc.status_code}: {detail}",
            )
        except anthropic.APIError as exc:
            return ProviderHealth(
                "anthropic", "error", model, f"Probe failed: {exc}"
            )
        latency = _elapsed_ms(start)
        limits = _limit_summary(headers, _ANTHROPIC_LIMITS)
        status = "ready"
        details.append(f"probe OK in {latency} ms")

    return ProviderHealth(
        "anthropic",
        status,
        model,
        "; ".join(details),
        latency_ms=latency,
        limits=limits,
    )


def _check_openai(config: Config, *, probe: bool) -> ProviderHealth:
    model = config.model or OpenAIProvider.default_model
    if not _available("openai"):
        return ProviderHealth(
            "openai",
            "not installed",
            model,
            "Install it with: pip install 'verso-cli[openai]'",
        )

    key = openai_api_key(config)
    if not key:
        return ProviderHealth("openai", "no credentials", model, _NO_OPENAI)

    import openai

    try:
        client = openai.OpenAI(api_key=key, timeout=_TIMEOUT, max_retries=0)
        models = [entry.id for entry in client.models.list().data]
    except openai.AuthenticationError:
        return ProviderHealth("openai", "auth failed", model, _NO_OPENAI)
    except openai.APIConnectionError as exc:
        return ProviderHealth(
            "openai", "unreachable", model, f"Could not reach the API: {exc}"
        )
    except openai.APIStatusError as exc:
        detail = getattr(exc, "message", str(exc))
        return ProviderHealth(
            "openai", "error", model, f"HTTP {exc.status_code}: {detail}"
        )
    except openai.OpenAIError as exc:
        return ProviderHealth("openai", "error", model, str(exc))

    status = "ready"
    details = [f"{len(models)} models available"]
    if models and not _model_available(model, models):
        status = "degraded"
        details.append(_available_models_detail(model, models))

    latency = None
    limits: dict[str, str] = {}
    if probe:
        start = time.perf_counter()
        try:
            raw = client.chat.completions.with_raw_response.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_completion_tokens=4,
            )
            headers = raw.headers
            raw.parse()
        except openai.RateLimitError:
            return ProviderHealth(
                "openai",
                "rate limited",
                model,
                "Credentials are valid, but the probe was rate limited.",
            )
        except openai.AuthenticationError:
            return ProviderHealth("openai", "auth failed", model, _NO_OPENAI)
        except openai.APIConnectionError as exc:
            return ProviderHealth("openai", "unreachable", model, str(exc))
        except openai.APIStatusError as exc:
            detail = getattr(exc, "message", str(exc))
            return ProviderHealth(
                "openai",
                "error",
                model,
                f"Probe failed: HTTP {exc.status_code}: {detail}",
            )
        except openai.OpenAIError as exc:
            return ProviderHealth("openai", "error", model, f"Probe failed: {exc}")
        latency = _elapsed_ms(start)
        limits = _limit_summary(headers, _OPENAI_LIMITS)
        status = "ready"
        details.append(f"probe OK in {latency} ms")

    return ProviderHealth(
        "openai",
        status,
        model,
        "; ".join(details),
        latency_ms=latency,
        limits=limits,
    )


def _ollama_version(host: str) -> str:
    try:
        with httpx.Client(timeout=5.0) as client:
            data = client.get(f"{host.rstrip('/')}/api/version").json()
    except (httpx.HTTPError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("version") or "")


def _check_ollama(config: Config, *, probe: bool) -> ProviderHealth:
    local = list_local_models(config.ollama_host)
    if local is None:
        return ProviderHealth(
            "ollama",
            "unreachable",
            "",
            f"Could not reach {config.ollama_host}. Is `ollama serve` running?",
        )

    cloud = list_cloud_models(timeout=5.0)
    version = _ollama_version(config.ollama_host)
    model = config.ollama_model or config.model
    if not model:
        model = local[0].name if local else (cloud[0].name if cloud else "")

    details = []
    if version:
        details.append(f"server {version}")
    details.append(f"{len(local)} local, {len(cloud)} cloud")

    status = "ready"
    if not model:
        status = "degraded"
        details.append("no models available")
    elif _is_cloud_model(model):
        status = "degraded"
        details.append("cloud")
        if not probe:
            details.append("sign-in unverified (use --probe)")
    elif model not in {entry.name for entry in local}:
        status = "degraded"
        details.append(f"{model!r} not installed")

    latency = None
    can_probe = probe and model and (
        status != "degraded" or _is_cloud_model(model)
    )
    if can_probe:
        start = time.perf_counter()
        try:
            llm = OllamaProvider(
                model=model, config=config, read_timeout=60.0
            )
            llm.complete(
                GenerationRequest(prompt="Reply with OK.", max_tokens=4)
            )
        except ProviderError as exc:
            return ProviderHealth(
                "ollama", "error", model, f"Probe failed: {exc}"
            )
        except Exception as exc:  # a health check should never crash
            return ProviderHealth(
                "ollama", "error", model, f"Probe failed: {exc}"
            )
        latency = _elapsed_ms(start)
        status = "ready"
        details.append(f"probe OK in {latency} ms")

    return ProviderHealth(
        "ollama",
        status,
        model,
        "; ".join(details),
        latency_ms=latency,
    )


_CHECKERS = {
    "anthropic": _check_anthropic,
    "openai": _check_openai,
    "ollama": _check_ollama,
}


def check_providers(
    config: Config, *, only: str | None = None, probe: bool = False
) -> list[ProviderHealth]:
    if only is not None:
        name = only.strip().lower()
        if name not in _CHECKERS:
            raise ProviderError(
                f"Unknown provider {name!r}. Choose one of: {', '.join(PROVIDERS)}."
            )
        names = [name]
    else:
        names = list(PROVIDERS)
    return [_CHECKERS[name](config, probe=probe) for name in names]
