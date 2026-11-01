"""User configuration.

Precedence for API keys: environment variable, then the config file.
The config file never needs to hold a key; env vars are the recommended path.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

PROVIDERS = ("anthropic", "openai", "ollama")


class Config(BaseModel):
    provider: str = "anthropic"
    model: str | None = None
    max_tokens: int = 8192
    words_per_chapter: int = 1500
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


def config_dir() -> Path:
    override = os.environ.get("VERSO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and (appdata := os.environ.get("APPDATA")):
        return Path(appdata) / "verso"
    return Path.home() / ".config" / "verso"


def config_path() -> Path:
    return config_dir() / "config.toml"


def load_config() -> Config:
    path = config_path()
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    return Config.model_validate(data)


def save_config(config: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Verso configuration. See `verso config show`.", ""]
    for name, value in config.model_dump().items():
        if value is None:
            continue
        lines.append(f"{name} = {_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # json.dumps produces a valid TOML basic string for our flat values.
    return json.dumps(str(value))


def anthropic_api_key(config: Config) -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or config.anthropic_api_key


def openai_api_key(config: Config) -> str | None:
    return os.environ.get("OPENAI_API_KEY") or config.openai_api_key
