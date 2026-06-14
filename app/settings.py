"""Рантайм-настройки, переключаемые из UI и переживающие рестарт.

Хранятся в /conf/.webui-settings.json. Сейчас одна настройка — safe_mode
(безопасный режим: паузы между Spotify-загрузками против бана)."""
import json
import os
import threading

from . import config

_PATH = os.path.join(config.CONF_DIR, ".webui-settings.json")
_lock = threading.Lock()
_cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        data = {"safe_mode": config.ZOTIFY_SAFE_MODE}
        try:
            with open(_PATH, "r", encoding="utf-8") as fh:
                data.update(json.load(fh))
        except (OSError, ValueError):
            pass
        _cache = data
    return _cache


def all_settings() -> dict:
    return dict(_load())


def get_safe_mode() -> bool:
    return bool(_load().get("safe_mode", True))


def set_safe_mode(value: bool) -> bool:
    with _lock:
        data = _load()
        data["safe_mode"] = bool(value)
        try:
            os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
            with open(_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass
    return get_safe_mode()
