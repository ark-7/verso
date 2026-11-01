import sys
import types

import pytest

from verso import health
from verso.config import Config
from verso.providers.base import ProviderError
from verso.providers.ollama import OllamaModel

AUTH_ERROR = (
    "Could not resolve authentication method. Expected one of api_key, "
    "auth_token, or credentials to be set."
)


class _FakeRaw:
    def __init__(self, headers=None):
        self.headers = headers or {}

    def parse(self):
        return object()


class _FakeAnthropicModels:
    def __init__(self, model_ids, error=None):
        self.model_ids = model_ids
        self.error = error

    def list(self, limit=None):
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(
            data=[
                types.SimpleNamespace(id=model_id)
                for model_id in self.model_ids
            ]
        )


class _FakeAnthropicMessages:
    def __init__(self):
        self.with_raw_response = self

    def create(self, **kwargs):
        return _FakeRaw(
            {
                "anthropic-ratelimit-requests-limit": "50",
                "anthropic-ratelimit-requests-remaining": "49",
                "anthropic-ratelimit-tokens-limit": "40000",
                "anthropic-ratelimit-tokens-remaining": "39900",
            }
        )


class _FakeAnthropicClient:
    def __init__(self, model_ids, list_error=None):
        self.models = _FakeAnthropicModels(model_ids, list_error)
        self.messages = _FakeAnthropicMessages()


def _fake_anthropic(model_ids=("claude-opus-5-5",), list_error=None):
    module = types.ModuleType("anthropic")
    module.Anthropic = lambda **kwargs: _FakeAnthropicClient(
        model_ids, list_error
    )
    module.AuthenticationError = type("AuthenticationError", (Exception,), {})
    module.APIConnectionError = type("APIConnectionError", (Exception,), {})
    module.APIStatusError = type("APIStatusError", (Exception,), {})
    module.APIError = type("APIError", (Exception,), {})
    module.RateLimitError = type(
        "RateLimitError", (module.APIStatusError,), {}
    )
    return module


class _FakeOpenAIModels:
    def __init__(self, model_ids):
        self.model_ids = model_ids

    def list(self):
        return types.SimpleNamespace(
            data=[
                types.SimpleNamespace(id=model_id)
                for model_id in self.model_ids
            ]
        )


class _FakeOpenAIClient:
    def __init__(self, model_ids):
        self.models = _FakeOpenAIModels(model_ids)


def _fake_openai(model_ids=("gpt-4o-mini",)):
    module = types.ModuleType("openai")
    module.OpenAI = lambda **kwargs: _FakeOpenAIClient(model_ids)
    module.AuthenticationError = type("AuthenticationError", (Exception,), {})
    module.APIConnectionError = type("APIConnectionError", (Exception,), {})
    module.APIStatusError = type("APIStatusError", (Exception,), {})
    module.RateLimitError = type("RateLimitError", (Exception,), {})
    module.OpenAIError = type("OpenAIError", (Exception,), {})
    return module


def test_model_available_matches_version_suffixes():
    assert health._model_available(
        "claude-opus-5-5", ["claude-opus-5-5-20260101"]
    )
    assert health._model_available(
        "claude-opus-5-5-20260101", ["claude-opus-5-5"]
    )
    assert not health._model_available(
        "claude-opus-5-5", ["claude-sonnet-5-5"]
    )


def test_anthropic_missing_sdk(monkeypatch):
    monkeypatch.setattr(health, "_available", lambda name: False)

    result = health._check_anthropic(Config(), probe=False)

    assert result.status == "not installed"
    assert not result.usable


def test_anthropic_ready_and_lists_models(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic())
    monkeypatch.setattr(health, "_available", lambda name: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = health._check_anthropic(Config(), probe=False)

    assert result.status == "ready"
    assert result.model == "claude-opus-5-5"
    assert "1 models available" in result.detail


def test_anthropic_degrades_when_model_is_not_listed(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic())
    monkeypatch.setattr(health, "_available", lambda name: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = health._check_anthropic(
        Config(model="claude-unlisted"), probe=False
    )

    assert result.status == "degraded"
    assert "not listed" in result.detail


def test_anthropic_probe_reports_limits(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic())
    monkeypatch.setattr(health, "_available", lambda name: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = health._check_anthropic(Config(), probe=True)

    assert result.status == "ready"
    assert result.latency_ms is not None
    assert result.limits["requests"] == "49 / 50"
    assert result.limits["tokens"] == "39900 / 40000"


def test_anthropic_lazy_auth_error_is_reported(monkeypatch):
    fake = _fake_anthropic(list_error=TypeError(AUTH_ERROR))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setattr(health, "_available", lambda name: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = health._check_anthropic(Config(), probe=False)

    assert result.status == "no credentials"
    assert "ANTHROPIC_API_KEY" in result.detail


def test_openai_reports_missing_key(monkeypatch):
    monkeypatch.setattr(health, "_available", lambda name: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = health._check_openai(Config(), probe=False)

    assert result.status == "no credentials"
    assert not result.usable
    assert "OPENAI_API_KEY" in result.detail


def test_openai_ready(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", _fake_openai())
    monkeypatch.setattr(health, "_available", lambda name: True)

    result = health._check_openai(
        Config(openai_api_key="test-key"), probe=False
    )

    assert result.status == "ready"
    assert result.model == "gpt-4o-mini"


def test_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(health, "list_local_models", lambda host: None)

    result = health._check_ollama(Config(), probe=False)

    assert result.status == "unreachable"
    assert "ollama serve" in result.detail


def test_ollama_ready_with_installed_model(monkeypatch):
    monkeypatch.setattr(
        health,
        "list_local_models",
        lambda host: [OllamaModel(name="gemma4:e4b", cloud=False)],
    )
    monkeypatch.setattr(health, "list_cloud_models", lambda timeout: [])
    monkeypatch.setattr(health, "_ollama_version", lambda host: "0.22.1")

    result = health._check_ollama(
        Config(ollama_model="gemma4:e4b"), probe=False
    )

    assert result.status == "ready"
    assert "0.22.1" in result.detail
    assert "1 local, 0 cloud" in result.detail


def test_ollama_degrades_when_model_is_not_installed(monkeypatch):
    monkeypatch.setattr(
        health,
        "list_local_models",
        lambda host: [OllamaModel(name="gemma4:e4b", cloud=False)],
    )
    monkeypatch.setattr(health, "list_cloud_models", lambda timeout: [])
    monkeypatch.setattr(health, "_ollama_version", lambda host: "")

    result = health._check_ollama(
        Config(ollama_model="llama3.1"), probe=False
    )

    assert result.status == "degraded"
    assert "not installed" in result.detail


def test_ollama_cloud_needs_a_probe_to_verify(monkeypatch):
    monkeypatch.setattr(health, "list_local_models", lambda host: [])
    monkeypatch.setattr(
        health,
        "list_cloud_models",
        lambda timeout: [OllamaModel(name="gemma4:cloud", cloud=True)],
    )
    monkeypatch.setattr(health, "_ollama_version", lambda host: "")

    result = health._check_ollama(
        Config(ollama_model="gemma4:cloud"), probe=False
    )

    assert result.status == "degraded"
    assert "unverified" in result.detail


def test_ollama_probe_verifies_inference(monkeypatch):
    monkeypatch.setattr(
        health,
        "list_local_models",
        lambda host: [OllamaModel(name="gemma4:e4b", cloud=False)],
    )
    monkeypatch.setattr(health, "list_cloud_models", lambda timeout: [])
    monkeypatch.setattr(health, "_ollama_version", lambda host: "0.22.1")

    class _FakeOllama:
        def __init__(self, model=None, config=None, read_timeout=None):
            self.model = model

        def complete(self, request):
            return "OK"

    monkeypatch.setattr(health, "OllamaProvider", _FakeOllama)

    result = health._check_ollama(
        Config(ollama_model="gemma4:e4b"), probe=True
    )

    assert result.status == "ready"
    assert result.latency_ms is not None
    assert "probe OK" in result.detail


def test_unknown_provider_is_rejected():
    with pytest.raises(ProviderError, match="Unknown provider"):
        health.check_providers(Config(), only="bogus")
