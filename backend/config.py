import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# On Railway (and similar PaaS), the project root may be read-only.
# Use DATA_DIR env var if set (e.g. /data via Railway volume), otherwise
# fall back to a writable temp-like location or the project root locally.
_DATA_DIR_ENV = os.environ.get("DATA_DIR", "")
if _DATA_DIR_ENV:
    DATA_DIR = Path(_DATA_DIR_ENV)
else:
    DATA_DIR = BASE_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)

TOKENS_FILE = DATA_DIR / "tokens.json"
CONFIG_FILE = DATA_DIR / "config.json"

# If config.json only exists at project root (legacy), copy it to DATA_DIR
_legacy_config = BASE_DIR / "config.json"
if not CONFIG_FILE.exists() and _legacy_config.exists() and DATA_DIR != BASE_DIR:
    import shutil
    shutil.copy2(str(_legacy_config), str(CONFIG_FILE))


def load_tokens():
    if not TOKENS_FILE.exists():
        return {}
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_tokens(data):
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def load_config():
    env_user = os.environ.get("ADMIN_USER")
    env_pass = os.environ.get("ADMIN_PASS")

    # Start with defaults or file-based config
    config = _load_config_from_file()

    # Environment variables always override file values (Railway-friendly)
    if env_user:
        config["admin_user"] = env_user
    if env_pass:
        config["admin_pass"] = env_pass

    return config


def _load_config_from_file() -> dict:
    if not CONFIG_FILE.exists():
        default_config = {
            "admin_user": "admin",
            "admin_pass": "admin123",
            "theme_accent": "#5865f2",
            "theme_bg": "#0f0f14"
        }
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        return default_config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure theme keys exist
            data.setdefault("theme_accent", "#5865f2")
            data.setdefault("theme_bg", "#0f0f14")
            return data
    except Exception:
        return {
            "admin_user": "admin",
            "admin_pass": "admin",
            "theme_accent": "#5865f2",
            "theme_bg": "#0f0f14"
        }


def save_config(data):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
