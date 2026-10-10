"""Configuration: environment variables, optional .env, and values saved in the GUI.

Precedence per field: DATA_DIR/config.json (GUI) > environment > .env file > default.
DATA_DIR itself only comes from the environment or .env, since config.json lives inside it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import LANGUAGES, set_language, t

OVERRIDES_FILE = "config.json"
# Format version of config.json. Raise it when a field is renamed or its meaning changes, and teach
# _migrate_overrides how to convert older files – configurations and backups from older versions keep working.
CONFIG_VERSION = 1
VERSION_KEY = "_config_version"

# Language model providers. Only Claude is tested; the others use the OpenAI-compatible API.
PROVIDERS = ("claude", "openai", "gemini", "ollama", "lmstudio")
RECOMMENDED_PROVIDER = "claude"
PROVIDER_NAMES = {"claude": "Claude", "openai": "OpenAI (ChatGPT)", "gemini": "Google Gemini",
                  "ollama": "Ollama", "lmstudio": "LM Studio"}
LOCAL_PROVIDERS = ("ollama", "lmstudio")
DEFAULT_URLS = {"openai": "https://api.openai.com/v1",
                "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
                "ollama": "http://localhost:11434", "lmstudio": "http://localhost:1234"}
# Which fields belong to which provider: API key ('key'), server address ('url') and model.
PROVIDER_FIELDS = {
    "claude": {"key": "ANTHROPIC_API_KEY", "model": "CLAUDE_MODEL"},
    "openai": {"key": "OPENAI_API_KEY", "model": "OPENAI_MODEL"},
    "gemini": {"key": "GEMINI_API_KEY", "model": "GEMINI_MODEL"},
    "ollama": {"url": "OLLAMA_URL", "model": "OLLAMA_MODEL"},
    "lmstudio": {"url": "LMSTUDIO_URL", "model": "LMSTUDIO_MODEL"},
}
# Streaming country when STREAMING_REGION is empty: the one most likely for the interface language.
REGION_FOR_LANGUAGE = {"de": "DE", "en": "US"}


@dataclass(frozen=True)
class Field:
    name: str
    group: str
    required: bool = False
    secret: bool = False
    default: str = ""
    kind: str = "str"  # 'str' | 'int' | 'url' | 'choice' | 'region' (country code) | 'ids' (e.g. "8,337")
    choices: tuple[str, ...] = ()
    app_restart: bool = False  # only takes effect after restarting the whole program
    editable: bool = True
    provider: str = ""  # only used (and only required) when this language model provider is selected
    live: bool = False  # takes effect without restarting the bot


FIELDS: tuple[Field, ...] = (
    Field("TELEGRAM_BOT_TOKEN", "telegram", required=True, secret=True),
    Field("TELEGRAM_CHAT_ID", "telegram", required=True, kind="int"),
    # Optional separate chat for Sonarr/Radarr download messages; empty = the group above.
    Field("NOTIFY_CHAT_ID", "telegram", kind="int"),
    Field("LLM_PROVIDER", "llm", default=RECOMMENDED_PROVIDER, kind="choice", choices=PROVIDERS),
    Field("ANTHROPIC_API_KEY", "llm", required=True, secret=True, provider="claude"),
    Field("CLAUDE_MODEL", "llm", default="claude-haiku-5-5", provider="claude"),
    Field("OPENAI_API_KEY", "llm", required=True, secret=True, provider="openai"),
    Field("OPENAI_MODEL", "llm", required=True, provider="openai"),
    Field("GEMINI_API_KEY", "llm", required=True, secret=True, provider="gemini"),
    Field("GEMINI_MODEL", "llm", required=True, provider="gemini"),
    Field("OLLAMA_URL", "llm", required=True, kind="url", default=DEFAULT_URLS["ollama"], provider="ollama"),
    Field("OLLAMA_MODEL", "llm", required=True, provider="ollama"),
    Field("LMSTUDIO_URL", "llm", required=True, kind="url", default=DEFAULT_URLS["lmstudio"],
          provider="lmstudio"),
    Field("LMSTUDIO_MODEL", "llm", required=True, provider="lmstudio"),
    Field("JELLYFIN_URL", "jellyfin", required=True, kind="url"),
    Field("JELLYFIN_API_KEY", "jellyfin", required=True, secret=True),
    Field("JELLYFIN_USER", "jellyfin", required=True),
    Field("JELLYSEERR_URL", "jellyseerr", required=True, kind="url"),
    Field("JELLYSEERR_API_KEY", "jellyseerr", required=True, secret=True),
    # Streaming services the household subscribes to: cards for new titles name the ones that have it.
    # Region empty = derived from LANGUAGE; no services = feature off.
    Field("STREAMING_REGION", "jellyseerr", kind="region", live=True),
    Field("STREAMING_PROVIDERS", "jellyseerr", kind="ids", live=True),
    Field("WEBHOOK_HOST", "advanced", default="0.0.0.0", app_restart=True),
    Field("WEBHOOK_PORT", "advanced", default="8787", kind="int", app_restart=True),
    # Not masked: it is made up here and has to be copied into the Jellyfin webhook plugin.
    Field("WEBHOOK_SECRET", "webhooks", required=True),
    # Optional: only saved when chosen in the setup assistant – lets the status page check Sonarr/Radarr
    # actively instead of only noticing when their messages arrive.
    Field("SONARR_URL", "webhooks", kind="url", live=True),
    Field("SONARR_API_KEY", "webhooks", secret=True, live=True),
    Field("RADARR_URL", "webhooks", kind="url", live=True),
    Field("RADARR_API_KEY", "webhooks", secret=True, live=True),
    Field("ADMIN_PASSWORD", "interface", secret=True),
    # Docker images follow the channel of their tag (CORSARR_CHANNEL), so there it is fixed.
    Field("UPDATE_CHANNEL", "interface", default="stable", kind="choice", choices=("stable", "beta", "dev"), live=True),
    Field("LANGUAGE", "interface", default="en", kind="choice", choices=LANGUAGES),
    Field("LOG_LEVEL", "advanced", default="INFO", kind="choice",
          choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    Field("DATA_DIR", "advanced", default="./data", editable=False, app_restart=True),
)
FIELD_BY_NAME = {f.name: f for f in FIELDS}


@dataclass(frozen=True)
class Config:
    values: dict[str, str]
    sources: dict[str, str]  # name -> 'gui' | 'env' | 'default'
    data_dir: Path
    errors: dict[str, str] = field(default_factory=dict)  # name -> message (missing or invalid)

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    @property
    def complete(self) -> bool:
        return not self.errors

    # --- typed accessors for the bot ------------------------------------
    telegram_token = property(lambda self: self.get("TELEGRAM_BOT_TOKEN"))
    anthropic_api_key = property(lambda self: self.get("ANTHROPIC_API_KEY"))
    llm_provider = property(lambda self: self.get("LLM_PROVIDER") or RECOMMENDED_PROVIDER)
    model = property(lambda self: self._llm("model"))
    llm_api_key = property(lambda self: self._llm("key"))
    llm_url = property(lambda self: self._llm("url").rstrip("/"))
    jellyfin_url = property(lambda self: self.get("JELLYFIN_URL").rstrip("/"))
    jellyfin_api_key = property(lambda self: self.get("JELLYFIN_API_KEY"))
    jellyfin_user = property(lambda self: self.get("JELLYFIN_USER"))
    jellyseerr_url = property(lambda self: self.get("JELLYSEERR_URL").rstrip("/"))
    jellyseerr_api_key = property(lambda self: self.get("JELLYSEERR_API_KEY"))
    webhook_host = property(lambda self: self.get("WEBHOOK_HOST"))
    webhook_secret = property(lambda self: self.get("WEBHOOK_SECRET"))
    admin_password = property(lambda self: self.get("ADMIN_PASSWORD"))
    language = property(lambda self: self.get("LANGUAGE"))
    log_level = property(lambda self: self.get("LOG_LEVEL"))
    streaming_region = property(lambda self: self.get("STREAMING_REGION").upper())

    @property
    def streaming_ids(self) -> set[int]:
        return set(parse_ids(self.get("STREAMING_PROVIDERS")) or [])

    def _llm(self, kind: str) -> str:
        name = PROVIDER_FIELDS.get(self.llm_provider, {}).get(kind)
        return self.get(name) if name else ""

    @property
    def chat_id(self) -> int:
        return int(self.get("TELEGRAM_CHAT_ID") or 0)

    @property
    def notify_chat_id(self) -> int:
        return int(self.get("NOTIFY_CHAT_ID") or 0) or self.chat_id

    @property
    def webhook_port(self) -> int:
        return int(self.get("WEBHOOK_PORT") or 8787)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "corsarr.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def overrides_path(self) -> Path:
        return self.data_dir / OVERRIDES_FILE


def validate(f: Field, value: str) -> str | None:
    """Error message for a value, or None when it is fine."""
    if not value:
        return t("cfg.missing", name=f.name) if f.required else None
    if f.kind == "int":
        try:
            int(value)
        except ValueError:
            return t("cfg.not_int", name=f.name, value=value)
    if f.kind == "url" and not value.startswith(("http://", "https://")):
        return t("cfg.not_url", name=f.name)
    if f.kind == "choice" and value not in f.choices:
        return t("cfg.not_choice", name=f.name, choices=", ".join(f.choices))
    if f.kind == "region" and not re.fullmatch(r"[A-Za-z]{2}", value):
        return t("cfg.not_region", name=f.name, value=value)
    if f.kind == "ids" and parse_ids(value) is None:
        return t("cfg.not_ids", name=f.name, value=value)
    return None


def parse_ids(value: str) -> list[int] | None:
    """"8, 337" -> [8, 337]; None when something else than numbers is in the list."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return [int(p) for p in parts] if all(p.isdigit() for p in parts) else None


def active(f: Field, values: dict[str, str]) -> bool:
    """Fields of a provider that is not selected are ignored (and not required)."""
    return not f.provider or f.provider == values.get("LLM_PROVIDER")


def read_env_file() -> dict[str, str]:
    env_file = Path(os.environ.get("CORSARR_ENV_FILE", ".env"))
    result: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def read_overrides(data_dir: Path) -> dict[str, str]:
    path = data_dir / OVERRIDES_FILE
    if not path.is_file():
        return {}
    data = _migrate_overrides(json.loads(path.read_text(encoding="utf-8")))
    return {k: str(v) for k, v in data.items() if k in FIELD_BY_NAME and FIELD_BY_NAME[k].editable}


def _migrate_overrides(data: dict) -> dict:
    """Bring a config.json written by an older version up to CONFIG_VERSION.

    Files from a newer version are read as they are: unknown fields are ignored, known ones still apply.
    """
    version = data.get(VERSION_KEY, 1)  # files from before versioning are version 1
    # A future rename would go here, e.g.:  if version < 2: data["NEW"] = data.pop("OLD", "")
    return data


def write_overrides(data_dir: Path, overrides: dict[str, str]) -> None:
    path = data_dir / OVERRIDES_FILE
    tmp = path.with_suffix(".tmp")
    data = {VERSION_KEY: CONFIG_VERSION, **{k: v for k, v in overrides.items() if k != VERSION_KEY}}
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)  # holds API keys
    os.replace(tmp, path)


def env_value(name: str, env_file: dict[str, str] | None = None) -> str | None:
    """Value from the environment, else from the .env file; None when unset or blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        value = (read_env_file() if env_file is None else env_file).get(name)
    return value.strip() if value and value.strip() else None


def _adopt_legacy_database(data_dir: Path) -> None:
    """The project used to be called Filmbot: take over its database instead of starting empty."""
    old, new = data_dir / "filmbot.db", data_dir / "corsarr.db"
    if old.exists() and not new.exists():
        for suffix in ("", "-wal", "-shm"):  # SQLite write-ahead files belong to the database
            src = old.with_name(old.name + suffix)
            if src.exists():
                src.rename(new.with_name(new.name + suffix))


def load() -> Config:
    """Load leniently: missing or invalid values end up in Config.errors instead of exiting."""
    env_file = read_env_file()

    def from_env(name: str) -> str | None:
        return env_value(name, env_file)

    data_dir = Path(from_env("DATA_DIR") or "./data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    _adopt_legacy_database(data_dir)
    overrides = read_overrides(data_dir)

    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for f in FIELDS:
        if f.name == "DATA_DIR":
            values[f.name], sources[f.name] = str(data_dir), "env" if from_env("DATA_DIR") else "default"
        elif overrides.get(f.name, "").strip():
            values[f.name], sources[f.name] = overrides[f.name].strip(), "gui"
        elif (env := from_env(f.name)) is not None:
            values[f.name], sources[f.name] = env, "env"
        else:
            values[f.name], sources[f.name] = f.default, "default"

    # Language first, so error messages already come out in it.
    set_language(values["LANGUAGE"])
    if not values["STREAMING_REGION"]:
        values["STREAMING_REGION"] = REGION_FOR_LANGUAGE.get(values["LANGUAGE"], "US")
    errors = {f.name: err for f in FIELDS if active(f, values) and (err := validate(f, values[f.name]))}
    return Config(values=values, sources=sources, data_dir=data_dir, errors=errors)
