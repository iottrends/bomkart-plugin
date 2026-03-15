"""
BOMKart Settings Manager

Persists plugin configuration to ~/.config/bomkart/settings.json.
Stores API URL, API key, delivery preferences, etc.
"""

import json
import os
from typing import Any


DEFAULT_SETTINGS = {
    "api_url": "https://api.bomkart.lambdauav.com/v1",
    "api_key": "",
    "city": "Delhi-NCR",
    "delivery_pincode": "",
    "customer_name": "",
    "customer_phone": "",
    "customer_email": "",
    "show_alternatives": True,
    "auto_check_on_open": False,
    "currency": "INR",
    "last_order_id": "",
}


def _config_dir() -> str:
    """Get BOMKart config directory, create if needed."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = os.path.join(base, "bomkart")
    os.makedirs(path, exist_ok=True)
    return path


def _settings_path() -> str:
    return os.path.join(_config_dir(), "settings.json")


class Settings:
    """Read/write BOMKart plugin settings."""

    def __init__(self):
        self._data: dict = {}
        self.load()

    def load(self):
        """Load settings from disk, merge with defaults."""
        self._data = dict(DEFAULT_SETTINGS)
        path = _settings_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        """Write current settings to disk."""
        path = _settings_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"BOMKart: Failed to save settings: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any):
        self._data[key] = value

    @property
    def api_url(self) -> str:
        return self._data.get("api_url", DEFAULT_SETTINGS["api_url"])

    @api_url.setter
    def api_url(self, val: str):
        self._data["api_url"] = val

    @property
    def api_key(self) -> str:
        return self._data.get("api_key", "")

    @api_key.setter
    def api_key(self, val: str):
        self._data["api_key"] = val
