"""Проверка окружения для баннера в UI: cookies, права записи, deno, версия spotdl.

Записываемость проверяется РЕАЛЬНОЙ записью временного файла (надёжнее, чем
os.access: при ACL/root-squash os.access врёт)."""
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from . import config, playlists
from .naming import is_saved

_spotdl_version: Optional[str] = None


def _writable(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".wtest.", dir=path)
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def _spotdl_ver() -> str:
    global _spotdl_version
    if _spotdl_version is None:
        try:
            out = subprocess.run([config.SPOTDL_BIN, "--version"],
                                 capture_output=True, text=True, timeout=15)
            _spotdl_version = (out.stdout or out.stderr).strip() or "?"
        except Exception:  # noqa: BLE001
            _spotdl_version = "недоступен"
    return _spotdl_version


def environment_status() -> dict:
    music = config.MUSIC_DIR
    spotify_dir = os.path.join(music, "spotify")
    m3u_dir = config.PLAYLISTS_M3U_DIR
    # для записи новых треков важна записываемость папки библиотеки
    music_writable = _writable(spotify_dir) if os.path.isdir(spotify_dir) else _writable(music)

    checks = {
        "cookies_present": os.path.exists(config.COOKIE_FILE),
        "music_writable": music_writable,
        "playlists_writable": _writable(m3u_dir) if os.path.isdir(m3u_dir) else _writable(music),
        "conf_writable": _writable(config.CONF_DIR),
        "deno": shutil.which("deno") is not None,
        "spotdl_version": _spotdl_ver(),
        "deezer_configured": bool(config.deezer_arl()),
        "zotify_configured": config.zotify_configured(),
        "spotify_user_login": config.spotify_user_logged_in(),
    }
    # человекочитаемые предупреждения для баннера
    warnings = []
    if not checks["cookies_present"]:
        warnings.append("Нет YouTube cookies — YouTube может требовать вход «не робот». Загрузите cookies.txt во вкладке «Настройки».")
    if not checks["music_writable"]:
        warnings.append("Папка музыки недоступна на запись (uid 998) — новые треки не сохранятся.")
    if not checks["playlists_writable"]:
        warnings.append("Папка playlists недоступна на запись — m3u не обновится.")
    if not checks["spotify_user_login"] and any(is_saved(p.url) for p in playlists.read_playlists()):
        step = ("задайте Spotify-приложение и войдите" if not config.spotify_app()[0]
                else "войдите в Spotify")
        warnings.append(f"Liked Songs не синхронизируются: {step} во вкладке «Настройки».")
    if not checks["deno"]:
        warnings.append("Deno не найден — загрузки с YouTube будут падать (AudioProviderError).")
    checks["warnings"] = warnings
    return checks
