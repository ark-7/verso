import pytest

import verso.providers.ollama as ollama
from verso.config import Config, save_config
from verso.providers import get_provider
from verso.providers.base import GenerationRequest, ProviderError
from verso.providers.ollama import (
    OllamaProvider,
    _describe_http_error,
    _is_cloud_model,
    list_cloud_models,
    list_local_models,
)

AUTH_ERROR = (
    "Could not resolve authentication method. Expected one of api_key, auth_token, "
    "or credentials to be set. Or for the `Authorization` header to be explicitly "
    "omitted"
)


class _BrokenStream:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        raise self.error

    def __exit__(self, *exc_info):
        return False


class _BrokenMessages:
    def __init__(self, error):
        self.error = error

    def stream(self, **kwargs):
        return _BrokenStream(self.error)


class _BrokenClient:
    def __init__(self, error):
        self.messages = _BrokenMessages(error)


def _provider(error):
    pytest.importorskip("anthropic")
    from verso.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(config=Config())
    provider._client = _BrokenClient(error)
    return provider


def test_anthropic_missing_credentials_is_explained(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    provider = _provider(TypeError(AUTH_ERROR))

    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        list(provider.stream(GenerationRequest(prompt="Once upon a time.")))


def test_other_type_errors_are_not_masked():
    provider = _provider(TypeError("unexpected keyword argument 'nope'"))

    with pytest.raises(TypeError, match="unexpected keyword"):
        list(provider.stream(GenerationRequest(prompt="Once upon a time.")))


def test_ollama_uses_an_installed_model_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(
        OllamaProvider, "_installed_models", lambda self: ["gemma4:e4b"]
    )

    assert OllamaProvider().model == "gemma4:e4b"


def test_ollama_falls_back_to_its_default_when_unreachable(monkeypatch):
    monkeypatch.setattr(OllamaProvider, "_installed_models", lambda self: None)

    assert OllamaProvider().model == "llama3.1"


def test_ollama_explains_an_uninstalled_configured_model(monkeypatch):
    monkeypatch.setattr(
        OllamaProvider, "_installed_models", lambda self: ["gemma4:e4b"]
    )
    provider = OllamaProvider(model="llama3.1")

    with pytest.raises(ProviderError, match="gemma4:e4b"):
        list(provider.stream(GenerationRequest(prompt="Once upon a time.")))


def test_ollama_memory_errors_suggest_a_smaller_model():
    message = _describe_http_error(
        500,
        '{"error":"model requires more system memory (6.7 GiB) than is '
        'available (3.1 GiB)"}',
    )

    assert "smaller model" in message


def test_ollama_cloud_auth_errors_mention_signin():
    message = _describe_http_error(401, '{"error":"unauthorized"}')

    assert "ollama signin" in message


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("gemma4:cloud", True),
        ("gpt-oss:120b-cloud", True),
        ("gemma4:e4b", False),
        ("mistral-large-4", False),
    ],
)
def test_cloud_model_detection(name, expected):
    assert _is_cloud_model(name) is expected


def test_cloud_model_skips_local_validation(monkeypatch):
    monkeypatch.setattr(OllamaProvider, "_installed_models", lambda self: [])
    provider = OllamaProvider(model="gemma4:cloud")

    assert provider._resolve_model() == "gemma4:cloud"


def test_list_cloud_models_filters_and_dedupes(monkeypatch):
    entries = [
        {"name": "gemma4:31b", "capabilities": ["completion", "vision"]},
        {"name": "gemma4", "capabilities": ["completion"]},
        {"name": "clef:27b", "capabilities": ["decision", "vision"]},
        {"name": "mistral-large-4", "capabilities": ["completion", "tools"]},
    ]
    monkeypatch.setattr(ollama, "_fetch_tags", lambda url, *, timeout: entries)

    models = list_cloud_models()

    assert [model.name for model in models] == [
        "gemma4:cloud",
        "mistral-large-4:cloud",
    ]
    assert all(model.cloud for model in models)


def test_list_local_models_reports_details(monkeypatch):
    entries = [
        {
            "name": "gemma4:e4b",
            "size": 9_600_000_000,
            "details": {"parameter_size": "8.0B"},
        }
    ]
    monkeypatch.setattr(ollama, "_fetch_tags", lambda url, *, timeout: entries)

    models = list_local_models()

    assert len(models) == 1
    assert models[0].name == "gemma4:e4b"
    assert models[0].parameter_size == "8.0B"
    assert models[0].cloud is False


def test_list_local_models_returns_none_when_unreachable(monkeypatch):
    monkeypatch.setattr(ollama, "_fetch_tags", lambda url, *, timeout: None)

    assert list_local_models() is None


def test_ollama_model_setting_takes_precedence_for_ollama_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VERSO_CONFIG_DIR", str(tmp_path))
    save_config(
        Config(
            provider="ollama",
            model="generic-model",
            ollama_model="gemma4:cloud",
        )
    )

    provider = get_provider()

    assert provider.model == "gemma4:cloud"


def test_generic_model_still_applies_to_ollama(tmp_path, monkeypatch):
    monkeypatch.setenv("VERSO_CONFIG_DIR", str(tmp_path))
    save_config(Config(provider="ollama", model="gemma4:e4b"))

    provider = get_provider()

    assert provider.model == "gemma4:e4b"
