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
# Битрейт конвертации (заливка файлов, Deezer, Spotify-апгрейд). 320k — максимум,
# что отдаёт Spotify (lossy Ogg → mp3). "auto"/"" — не форсировать (ffmpeg default).
UPLOAD_BITRATE = os.environ.get("UPLOAD_BITRATE", "320k")

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
# Безопасный режим (анти-бан) = качать со СКОРОСТЬЮ ПРОСЛУШИВАНИЯ (real-time).
# Массовая закачка и апгрейд — всегда real-time; одиночные «Spotify 320k» —
# real-time только при включённой галочке. Тумблер есть в UI.
ZOTIFY_SAFE_MODE = os.environ.get("ZOTIFY_SAFE_MODE", "true").strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        return default


# Сколько параллельных загрузок держит сам spotdl. По умолчанию у него 4, и на
# каждую завершённую загрузку запускается ffmpeg — на слабом NAS это забивает
# все ядра. 2 — компромисс: скорость почти та же (упор в сеть), CPU вдвое ниже.
SPOTDL_THREADS = _int_env("SPOTDL_THREADS", 2)


# Idle-пауза (сек) МЕЖДУ треками поверх real-time (как Zotify bulk_wait_time).
# Применяется к апгрейду (всегда) и к массовой «Скачать все» (если безопасный режим).
ZOTIFY_BULK_WAIT_MIN = _int_env("ZOTIFY_BULK_WAIT_MIN", 5)
ZOTIFY_BULK_WAIT_MAX = _int_env("ZOTIFY_BULK_WAIT_MAX", 15)


# Апгрейд (Step 2): пропускать файлы, у которых битрейт уже >= порога (kbps).
# По умолчанию 300, чтобы файлы ~320k не апгрейдились повторно, а старые (128-160k)
# обновлялись. Согласовано с UPLOAD_BITRATE=320k.
UPGRADE_MIN_BITRATE = _int_env("UPGRADE_MIN_BITRATE", 300)
# Лимит апгрейд-загрузок в сутки (анти-бан). 0 = без лимита (real-time и так медленно).
UPGRADE_PER_DAY = _int_env("UPGRADE_PER_DAY", 0)

# Таймаут (сек) на ОДИН подпроцесс-скачивание трека (Deezer/Spotify/YouTube/заливка).
# Сторож убивает зависший процесс (напр. оборванную librespot-сессию посреди стрима),
# чтобы он не заморозил всю очередь. ВАЖНО: real-time-загрузка идёт со скоростью
# прослушивания и МОЛЧИТ весь трек, поэтому таймаут — щедрый потолок (час), иначе
# длинный честный трек (микс/сет/классика) убьётся как «зависший». 0 = без таймаута.
DOWNLOAD_TRACK_TIMEOUT = _int_env("DOWNLOAD_TRACK_TIMEOUT", 3600)


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
