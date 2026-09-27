"""Рантайм-настройки, переключаемые из UI и переживающие рестарт.

Хранятся в /conf/.webui-settings.json: safe_mode (безопасный режим: паузы между
Spotify-загрузками против бана). Отдельно — своё Spotify-приложение для Liked
Songs: секрет, поэтому лежит своим файлом /conf/spotify_app.json (mode 600)."""
import json
import os
import re
import tempfile
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


def _set(key: str, value) -> None:
    with _lock:
        data = _load()
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        try:
            os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
            with open(_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass


def set_safe_mode(value: bool) -> bool:
    _set("safe_mode", bool(value))
    return get_safe_mode()


# ------------------------------------------------------------------ Spotify-приложение
# client_id и secret у Spotify — 32 hex-символа; проверка ловит ошибки вставки.
_SPOTIFY_KEY_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _spotify_env_set() -> bool:
    return bool(config.SPOTIFY_CLIENT_ID.strip() and config.SPOTIFY_CLIENT_SECRET.strip())


def spotify_app_status() -> dict:
    """Для UI. Секрет наружу НЕ отдаём — только факт, что он сохранён."""
    cid, secret = config.spotify_app()
    logged_in = config.spotify_user_logged_in()
    return {
        "source": "env" if _spotify_env_set() else ("file" if cid else ""),
        "client_id": cid,
        "has_secret": bool(secret),
        "logged_in": logged_in,
        # имя запоминается при входе из UI; при входе из консоли его нет
        "user": _load().get("spotify_user", "") if logged_in else "",
    }


def set_spotify_user(name: str) -> None:
    _set("spotify_user", name or None)


def _drop_user_token() -> bool:
    """Удалить OAuth-токен входа: refresh-токен привязан к приложению, выдавшему
    его, и после смены/удаления приложения всё равно не обновится."""
    _set("spotify_user", None)
    try:
        os.remove(config.SPOTIFY_USER_TOKEN_FILE)
        return True
    except OSError:
        return False


def logout_spotify() -> dict:
    _drop_user_token()
    return spotify_app_status()


def set_spotify_app(client_id: str, client_secret: str) -> dict:
    """Сохранить своё Spotify-приложение. Пустой secret = оставить сохранённый
    (только если client_id не меняется). Возвращает статус + relogin_required."""
    if _spotify_env_set():
        raise ValueError("Задано через env SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET — "
                         "оно важнее настроек UI. Уберите env, чтобы править здесь.")
    cid, secret = client_id.strip(), client_secret.strip()
    old_cid, old_secret = config.spotify_app()
    if not secret and cid == old_cid:
        secret = old_secret
    if not _SPOTIFY_KEY_RE.match(cid):
        raise ValueError("Client ID — 32 символа 0-9/a-f (скопируйте из Settings приложения)")
    if not _SPOTIFY_KEY_RE.match(secret):
        raise ValueError("Client secret — 32 символа 0-9/a-f (кнопка «View client secret»)")

    with _lock:
        path = config.SPOTIFY_APP_FILE
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".spotify_app.", suffix=".tmp")  # mode 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"client_id": cid, "client_secret": secret}, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    # токен, выданный другому приложению (в т.ч. общему spotdl'овскому, когда
    # своего ещё не было), не обновится — вход придётся повторить
    relogin = old_cid != cid and _drop_user_token()
    return dict(spotify_app_status(), relogin_required=relogin)


def clear_spotify_app() -> dict:
    if _spotify_env_set():
        raise ValueError("Задано через env — удалить из UI нельзя.")
    with _lock:
        try:
            os.remove(config.SPOTIFY_APP_FILE)
        except OSError:
            pass
    _drop_user_token()
    return spotify_app_status()
