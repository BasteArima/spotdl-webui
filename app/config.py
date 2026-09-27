"""Конфигурация сервиса. Все пути и шаблоны берутся из окружения,
но по умолчанию совпадают с тем, что использует контейнер автосинка."""
import os

# Корни томов внутри контейнера
CONF_DIR = os.environ.get("CONF_DIR", "/conf")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")

# Файлы / папки данных spotdl
PLAYLISTS_FILE = os.environ.get("PLAYLISTS_FILE", os.path.join(CONF_DIR, "playlists.txt"))
COOKIE_FILE = os.environ.get("COOKIE_FILE", os.path.join(CONF_DIR, "cookies.txt"))
ERRORS_DIR = os.environ.get("ERRORS_DIR", os.path.join(CONF_DIR, "errors"))
LOCKS_DIR = os.environ.get("LOCKS_DIR", os.path.join(CONF_DIR, ".webui-locks"))
UPLOADS_DIR = os.environ.get("UPLOADS_DIR", os.path.join(CONF_DIR, ".webui-uploads"))

# Папки музыки
PLAYLISTS_M3U_DIR = os.environ.get("PLAYLISTS_M3U_DIR", os.path.join(MUSIC_DIR, "playlists"))

# Шаблоны вывода — ДОЛЖНЫ совпадать с автосинком, иначе папки/плейлисты разъедутся
OUTPUT_TEMPLATE = os.environ.get(
    "OUTPUT_TEMPLATE",
    "/music/spotify/{album-artist}/{album}/{track-number} - {title}.{output-ext}",
)
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "mp3")
# Настраиваемые из UI параметры (битрейт, потоки, паузы, лимиты, автосинк…)
# живут в app/settings.py: env для них — значение ПО УМОЛЧАНИЮ, UI переопределяет.

# Авторизация (см. app/auth.py). AUTH_ENABLED=false — вход отключён целиком
# (сервис только в локалке). Пароль: APP_PASSWORD (старое имя APP_AUTH_TOKEN
# тоже принимается); если не задан — пароль придумывается в UI при первом входе.
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
APP_PASSWORD = (os.environ.get("APP_PASSWORD") or os.environ.get("APP_AUTH_TOKEN") or "").strip()

# Путь к исполняемому spotdl (в образе доступен в PATH)
SPOTDL_BIN = os.environ.get("SPOTDL_BIN", "spotdl")

# Deezer ARL (секрет уровня пароля) — для фолбэка по треккам, которых нет на
# YouTube. Подаётся через env DEEZER_ARL или файл (в образ не попадает).
DEEZER_ARL = os.environ.get("DEEZER_ARL", "")
DEEZER_ARL_FILE = os.environ.get("DEEZER_ARL_FILE", os.path.join(CONF_DIR, "deezer_arl.txt"))


def deezer_arl() -> str:
    """ARL из окружения (приоритет) или из файла /conf/deezer_arl.txt."""
    val = DEEZER_ARL.strip()
    if val:
        return val
    try:
        with open(DEEZER_ARL_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- Zotify (реальное аудио со Spotify, 320k через Premium) -------------------
# Креды: файл credentials.json (предпочтительно) ИЛИ логин/пароль Spotify.
ZOTIFY_CREDENTIALS_FILE = os.environ.get(
    "ZOTIFY_CREDENTIALS_FILE", os.path.join(CONF_DIR, "zotify_credentials.json"))
ZOTIFY_USERNAME = os.environ.get("ZOTIFY_USERNAME", "")
ZOTIFY_PASSWORD = os.environ.get("ZOTIFY_PASSWORD", "")


# --- Liked Songs (spotdl-запрос `saved`) --------------------------------------
# У «Любимых треков» нет ссылки: spotdl качает их по запросу `saved`, и только
# с OAuth-входом пользователя (--user-auth). Токен с refresh'ем кэширует spotipy
# в этом файле; создаётся один раз командой `python -m app.spotify_login`.
SPOTIFY_USER_TOKEN_FILE = os.environ.get(
    "SPOTIFY_USER_TOKEN_FILE", os.path.join(CONF_DIR, ".spotify-user-token.json"))
# Своё приложение Spotify (developer.spotify.com) для этого входа. Общий
# client_id spotdl в dev-режиме пускает только allowlist → нужен свой.
# Env или файл /conf/spotify_app.json: {"client_id": "...", "client_secret": "..."}.
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_APP_FILE = os.environ.get("SPOTIFY_APP_FILE", os.path.join(CONF_DIR, "spotify_app.json"))


def spotify_app() -> tuple:
    """(client_id, client_secret) своего Spotify-приложения: env (приоритет) →
    /conf/spotify_app.json → ("", ""), тогда используется дефолт spotdl."""
    cid, secret = SPOTIFY_CLIENT_ID.strip(), SPOTIFY_CLIENT_SECRET.strip()
    if cid and secret:
        return cid, secret
    try:
        import json
        with open(SPOTIFY_APP_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cid = str(data.get("client_id") or "").strip()
        secret = str(data.get("client_secret") or "").strip()
        if cid and secret:
            return cid, secret
    except (OSError, ValueError, AttributeError):
        pass
    return "", ""


def spotify_user_logged_in() -> bool:
    return os.path.exists(SPOTIFY_USER_TOKEN_FILE)


def zotify_configured() -> bool:
    if os.path.exists(ZOTIFY_CREDENTIALS_FILE):
        return True
    return bool(ZOTIFY_USERNAME.strip() and ZOTIFY_PASSWORD.strip())


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Фоновый автосинк: интервал и «при старте» — в app/settings.py (меняются из UI).
# Задержка перед первым автосинком после старта (сек), чтобы веб успел подняться.
AUTOSYNC_START_DELAY = _env_float("AUTOSYNC_START_DELAY", 20.0)

# m3u-расширение, которое пишет автосинк
M3U_EXT = os.environ.get("M3U_EXT", "m3u8")


def m3u_path(safe: str) -> str:
    return os.path.join(PLAYLISTS_M3U_DIR, f"{safe}.{M3U_EXT}")


def errors_path(safe: str) -> str:
    return os.path.join(ERRORS_DIR, f"{safe}.txt")


def savefile_path(playlist_id: str) -> str:
    return os.path.join(CONF_DIR, f"{playlist_id}.spotdl")
