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
# Битрейт конвертации залитых вручную файлов. "auto"/"" — не форсировать (ffmpeg
# по умолчанию). По умолчанию 256k, чтобы не сильно ронять качество исходника.
UPLOAD_BITRATE = os.environ.get("UPLOAD_BITRATE", "256k")

# Авторизация. Без токена сервис стартовать не должен (см. main.py).
APP_AUTH_TOKEN = os.environ.get("APP_AUTH_TOKEN", "")

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
# very_high=320k (нужен Premium), high=160k, normal=96k, auto=макс. для аккаунта
ZOTIFY_QUALITY = os.environ.get("ZOTIFY_QUALITY", "very_high")
# Безопасный режим: пауза между Spotify-загрузками (анти-бан). Включён по умолчанию.
ZOTIFY_SAFE_MODE = os.environ.get("ZOTIFY_SAFE_MODE", "true").strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


ZOTIFY_GAP_MIN = _float_env("ZOTIFY_GAP_MIN", 20.0)   # мин. секунд между загрузками
ZOTIFY_GAP_MAX = _float_env("ZOTIFY_GAP_MAX", 45.0)   # макс. секунд между загрузками
# Качать со скоростью прослушивания (анти-бан, но медленно). По умолчанию выкл
# для ручных одиночных треков; фоновый массовый апгрейд будет включать.
ZOTIFY_REAL_TIME = os.environ.get("ZOTIFY_REAL_TIME", "").strip().lower() in ("1", "true", "yes", "on")


def zotify_configured() -> bool:
    if os.path.exists(ZOTIFY_CREDENTIALS_FILE):
        return True
    return bool(ZOTIFY_USERNAME.strip() and ZOTIFY_PASSWORD.strip())


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Фоновый автосинк (замена отдельного контейнера-автосинка).
# Интервал в часах; 0 или отрицательное — планировщик выключен.
AUTOSYNC_INTERVAL_HOURS = _env_float("AUTOSYNC_INTERVAL_HOURS", 24.0)
# Запускать ли один sync-всех вскоре после старта контейнера.
AUTOSYNC_ON_START = _env_bool("AUTOSYNC_ON_START", True)
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
