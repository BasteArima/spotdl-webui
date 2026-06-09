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

# Папки музыки
PLAYLISTS_M3U_DIR = os.environ.get("PLAYLISTS_M3U_DIR", os.path.join(MUSIC_DIR, "playlists"))

# Шаблоны вывода — ДОЛЖНЫ совпадать с автосинком, иначе папки/плейлисты разъедутся
OUTPUT_TEMPLATE = os.environ.get(
    "OUTPUT_TEMPLATE",
    "/music/spotify/{album-artist}/{album}/{track-number} - {title}.{output-ext}",
)
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "mp3")

# Авторизация. Без токена сервис стартовать не должен (см. main.py).
APP_AUTH_TOKEN = os.environ.get("APP_AUTH_TOKEN", "")

# Путь к исполняемому spotdl (в образе доступен в PATH)
SPOTDL_BIN = os.environ.get("SPOTDL_BIN", "spotdl")


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
