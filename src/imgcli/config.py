"""Configuration and path resolution for imgcli."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

APP = "imgcli"

DEFAULTS = {
    "outdir": "output",
    "width": 1024,
    "height": 1024,
    "model": "gemini-2.5-flash-image",
    "provider": "auto",  # auto | web | api
    "web_timeout": 180,
    "timeout": 120,
}

GEMINI_MODELS = (
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
    "gemini-2.0-flash-preview-image-generation",
)


def config_dir() -> Path:
    return Path(os.environ.get("IMGCLI_CONFIG_DIR", Path.home() / ".config" / APP))


def config_file() -> Path:
    return config_dir() / "config.toml"


def profile_dir() -> Path:
    """Persistent Chromium profile directory (keeps the session alive long term)."""
    return Path(os.environ.get("IMGCLI_PROFILE", config_dir() / "chrome-profile"))


def flow_profile_dir() -> Path:
    """Profil Chromium persisten khusus Flow (agar Gemini & Flow bisa paralel)."""
    return Path(os.environ.get("IMGCLI_FLOW_PROFILE", config_dir() / "chrome-flow"))


def default_storage_path() -> Path:
    """Where the saved Google session lives."""
    env = os.environ.get("IMGCLI_STORAGE")
    if env:
        return Path(env)
    cfg = config_dir() / "storage_state.json"
    if cfg.exists():
        return cfg
    local = Path.cwd() / "storage_state.json"
    if local.exists():
        return local
    return cfg


def get_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def load_config() -> dict:
    """Merge defaults with ~/.config/imgcli/config.toml (if present)."""
    cfg = dict(DEFAULTS)
    path = config_file()
    if path.exists():
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            section = data.get("imgcli", data)
            for key in DEFAULTS:
                if key in section:
                    cfg[key] = section[key]
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"warning: could not read {path}: {exc}", file=sys.stderr)
    return cfg
