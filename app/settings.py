"""Рантайм-настройки, которые меняются из UI (вкладка «Настройки») и переживают
рестарт. Хранятся ПЕРЕОПРЕДЕЛЕНИЯ в /conf/.webui-settings.json.

Приоритет для обычных настроек: значение из UI → env → встроенный дефолт. То есть
env (compose) задаёт значение ПО УМОЛЧАНИЮ, а UI его переопределяет; «сбросить»
в UI убирает переопределение и возвращает env/дефолт.

Секреты (Spotify-приложение, Deezer ARL) — наоборот: env важнее UI, чтобы
заданный админом в compose секрет нельзя было подменить из браузера. Они лежат
своими файлами с правами 600, не в общем json."""
import json
import os
import re
import tempfile
import threading
from typing import Any, Dict, List, Optional

from . import config

_PATH = os.path.join(config.CONF_DIR, ".webui-settings.json")
_lock = threading.Lock()
_cache = None


class Field:
    """Описание настройки: откуда дефолт (env), как проверять и как показать в UI."""

    def __init__(self, key: str, env: str, kind: str, default: Any, label: str,
                 group: str, help: str = "", lo: float = None, hi: float = None,
                 choices: List[str] = None, unit: str = "", labels: Dict[str, str] = None):
        self.key, self.env, self.kind, self.default = key, env, kind, default
        self.label, self.group, self.help, self.unit = label, group, help, unit
        self.lo, self.hi, self.choices = lo, hi, choices
        self.labels = labels or {}      # подписи вариантов choice для UI

    def parse(self, raw: Any) -> Any:
        """Привести значение (из json UI или строки env) к типу; ValueError — если нельзя."""
        if self.kind == "bool":
            if isinstance(raw, bool):
                return raw
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"«{self.label}»: ожидается да/нет")
        if self.kind == "choice":
            v = str(raw).strip()
            if v not in self.choices:
                raise ValueError(f"«{self.label}»: допустимо {', '.join(self.choices)}")
            return v
        try:
            v = float(raw) if self.kind == "float" else int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"«{self.label}»: нужно число") from None
        if (self.lo is not None and v < self.lo) or (self.hi is not None and v > self.hi):
            raise ValueError(f"«{self.label}»: от {self.lo:g} до {self.hi:g}")
        return v


G_SYNC, G_YT, G_DL, G_SP, G_UPG, G_SVC = ("Автосинк", "YouTube (spotdl)", "Загрузка",
                                         "Spotify (librespot)", "Апгрейд 320k", "Сервис")
FIELDS: List[Field] = [
    Field("autosync_interval_hours", "AUTOSYNC_INTERVAL_HOURS", "float", 24.0,
          "Интервал автосинка", G_SYNC, "Синхронизация всех плейлистов по расписанию. 0 — выключить.",
          lo=0, hi=720, unit="ч"),
    Field("autosync_on_start", "AUTOSYNC_ON_START", "bool", True,
          "Автосинк после старта контейнера", G_SYNC,
          "Один синк всех плейлистов вскоре после запуска, если автосинк включён "
          "(действует со следующего старта)."),
    Field("autosync_start_delay", "AUTOSYNC_START_DELAY", "int", 20,
          "Задержка автосинка после старта", G_SYNC, "Чтобы веб успел подняться.",
          lo=0, hi=3600, unit="с"),
    # --- параметры spotdl для YouTube (раньше не передавались → умолчания spotdl)
    Field("youtube_bitrate", "YOUTUBE_BITRATE", "choice", "auto",
          "Битрейт mp3 с YouTube", G_YT,
          "YouTube отдаёт звук ~128–160k: «как у источника» не пережимает лишний раз. "
          "Раньше было 128k — умолчание spotdl.",
          choices=["auto", "128k", "160k", "192k", "256k", "320k"],
          labels={"auto": "как у источника (auto)"}),
    Field("audio_providers", "AUDIO_PROVIDERS", "choice", "youtube-music",
          "Где искать аудио", G_YT, "Порядок = приоритет; второй источник — запасной.",
          choices=["youtube-music", "youtube", "youtube-music,youtube"],
          labels={"youtube-music": "YouTube Music", "youtube": "YouTube",
                  "youtube-music,youtube": "YouTube Music, затем YouTube"}),
    Field("lyrics_providers", "LYRICS_PROVIDERS", "choice", "genius,azlyrics,musixmatch",
          "Тексты песен", G_YT,
          "Вшиваются в теги. Синхронизированные (с таймкодами) показывает Navidrome, "
          "но не все плееры.",
          choices=["genius,azlyrics,musixmatch", "synced,musixmatch,genius", "off"],
          labels={"genius,azlyrics,musixmatch": "обычные (Genius, AZLyrics, Musixmatch)",
                  "synced,musixmatch,genius": "синхронизированные, иначе обычные",
                  "off": "не искать"}),
    Field("generate_lrc", "GENERATE_LRC", "bool", False,
          "Файлы .lrc рядом с треками", G_YT,
          "Отдельный файл с синхронизированным текстом (Navidrome его подхватывает). "
          "Включает поиск синхронизированных текстов."),
    Field("overwrite", "OVERWRITE", "choice", "skip",
          "Если файл уже скачан", G_YT, "Для sync и скачивания с YouTube.",
          choices=["skip", "metadata", "force"],
          labels={"skip": "пропустить", "metadata": "обновить только теги",
                  "force": "перекачать"}),
    Field("spotdl_threads", "SPOTDL_THREADS", "int", 2,
          "Параллельных загрузок spotdl", G_DL,
          "На каждую загрузку — свой ffmpeg. Больше — быстрее, но выше нагрузка на CPU.", lo=1, hi=8),
    Field("upload_bitrate", "UPLOAD_BITRATE", "choice", "320k",
          "Битрейт mp3 (заливка, Deezer, Spotify)", G_DL,
          "Для заливки файлов, Deezer и Spotify-загрузок. 320k — максимум Spotify. "
          "Для YouTube — отдельная настройка выше.",
          choices=["128k", "192k", "256k", "320k", "auto"]),
    Field("download_track_timeout", "DOWNLOAD_TRACK_TIMEOUT", "int", 3600,
          "Таймаут на один трек", G_DL,
          "Зависшую загрузку убьёт сторож. Real-time качается со скоростью прослушивания — "
          "не ставьте меньше длины длинных треков. 0 — без таймаута.", lo=0, hi=86400, unit="с"),
    Field("zotify_quality", "ZOTIFY_QUALITY", "choice", "very_high",
          "Качество Spotify", G_SP, "very_high = 320k (нужен Premium), high = 160k, normal = 96k.",
          choices=["very_high", "high", "normal"]),
    Field("safe_mode", "ZOTIFY_SAFE_MODE", "bool", True,
          "Безопасный режим", G_SP,
          "Одиночные «Spotify 320k» — в real-time (скорость прослушивания, анти-бан). "
          "Массовая закачка и апгрейд — всегда real-time. Тумблер есть и в шапке."),
    Field("bulk_wait_min", "ZOTIFY_BULK_WAIT_MIN", "int", 5,
          "Пауза между треками: от", G_SP, "Случайная пауза поверх real-time при массовых загрузках.",
          lo=0, hi=3600, unit="с"),
    Field("bulk_wait_max", "ZOTIFY_BULK_WAIT_MAX", "int", 15,
          "Пауза между треками: до", G_SP, "0 — без паузы.", lo=0, hi=3600, unit="с"),
    Field("upgrade_min_bitrate", "UPGRADE_MIN_BITRATE", "int", 300,
          "Не апгрейдить файлы от", G_UPG, "Файлы с таким битрейтом и выше считаются уже хорошими.",
          lo=0, hi=2000, unit="kbps"),
    Field("upgrade_per_day", "UPGRADE_PER_DAY", "int", 0,
          "Лимит апгрейда в сутки", G_UPG, "Анти-бан. 0 — без лимита (только паузы).", lo=0, hi=100000),
    Field("upgrade_track_timeout", "UPGRADE_TRACK_TIMEOUT", "int", 1200,
          "Таймаут апгрейда на трек", G_UPG, "Потом воркер перезапускается.", lo=60, hi=86400, unit="с"),
    Field("jobs_history", "JOBS_HISTORY", "int", 400,
          "Хранить завершённых задач", G_SVC,
          "Старые вытесняются (задачи активной пакетной загрузки — нет). Больше — больше памяти.",
          lo=20, hi=5000),
    Field("finished_log_lines", "FINISHED_LOG_LINES", "int", 300,
          "Строк лога у завершённой задачи", G_SVC,
          "Хвост с ошибкой; полный вывод всегда есть в docker logs.", lo=20, hi=4000),
    Field("session_ttl_days", "SESSION_TTL_DAYS", "int", 30,
          "Срок жизни входа", G_SVC, "Сколько дней браузер остаётся залогиненным. "
          "Действует на новые входы.", lo=1, hi=365, unit="дн"),
]
_BY_KEY: Dict[str, Field] = {f.key: f for f in FIELDS}


def _load() -> dict:
    """Сохранённые переопределения (и служебные ключи вроде spotify_user)."""
    global _cache
    if _cache is None:
        try:
            with open(_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _write(data: dict) -> None:
    global _cache
    os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, _PATH)
    _cache = data


def _set(key: str, value) -> None:
    with _lock:
        data = dict(_load())
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        try:
            _write(data)
        except OSError:
            pass


def _env_value(f: Field) -> Optional[Any]:
    raw = os.environ.get(f.env)
    if raw is None or not raw.strip():
        return None
    try:
        return f.parse(raw)
    except ValueError:
        return None      # кривое значение в env — молча берём встроенный дефолт


def default(key: str) -> Any:
    f = _BY_KEY[key]
    v = _env_value(f)
    return f.default if v is None else v


def get(key: str) -> Any:
    """Действующее значение: UI → env → дефолт."""
    f = _BY_KEY[key]
    stored = _load().get(key)
    if stored is not None:
        try:
            return f.parse(stored)
        except ValueError:
            pass        # руками испорченный json — не валим сервис
    return default(key)


def all_settings() -> dict:
    return {f.key: get(f.key) for f in FIELDS}


def schema() -> List[dict]:
    """Для UI: поля с текущими значениями, дефолтом и его источником."""
    stored = _load()
    out = []
    for f in FIELDS:
        env_v = _env_value(f)
        out.append({
            "key": f.key, "label": f.label, "group": f.group, "help": f.help,
            "kind": f.kind, "choices": f.choices, "labels": f.labels,
            "lo": f.lo, "hi": f.hi, "unit": f.unit,
            "value": get(f.key),
            "default": f.default if env_v is None else env_v,
            "default_source": "env" if env_v is not None else "builtin",
            "env": f.env,
            "overridden": stored.get(f.key) is not None,
        })
    return out


def update(values: Dict[str, Any]) -> dict:
    """Сохранить значения из UI. None — сбросить к env/дефолту. Всё или ничего:
    при ошибке в одном поле не сохраняется ни одно."""
    parsed = {}
    for key, raw in values.items():
        f = _BY_KEY.get(key)
        if f is None:
            raise ValueError(f"Неизвестная настройка: {key}")
        parsed[key] = None if raw is None else f.parse(raw)

    def effective(k):
        if k in parsed:
            return default(k) if parsed[k] is None else parsed[k]
        return get(k)
    lo, hi = effective("bulk_wait_min"), effective("bulk_wait_max")
    if hi and lo > hi:
        raise ValueError("Пауза «от» не может быть больше «до»")

    with _lock:
        data = dict(_load())
        for key, v in parsed.items():
            if v is None or v == default(key):
                data.pop(key, None)       # совпало с дефолтом — не храним переопределение
            else:
                data[key] = v
        _write(data)
    return all_settings()


def get_safe_mode() -> bool:
    return bool(get("safe_mode"))


def set_safe_mode(value: bool) -> bool:
    update({"safe_mode": bool(value)})
    return get_safe_mode()


# ------------------------------------------------------------------ системные (только env)
def system_info() -> List[dict]:
    """Что меняется только через env/compose (пути, шаблоны, uid) — показываем
    в UI read-only, чтобы было видно, с чем работает сервис."""
    return [
        {"env": "OUTPUT_TEMPLATE", "value": config.OUTPUT_TEMPLATE,
         "help": "Должен совпадать с уже скачанной библиотекой — иначе папки разъедутся"},
        {"env": "AUDIO_FORMAT", "value": config.AUDIO_FORMAT, "help": ""},
        {"env": "MUSIC_DIR", "value": config.MUSIC_DIR, "help": ""},
        {"env": "CONF_DIR", "value": config.CONF_DIR, "help": ""},
        {"env": "PUID:PGID", "value": f"{os.environ.get('PUID', '998')}:{os.environ.get('PGID', '100')}",
         "help": "Под этим uid:gid пишутся файлы (как у Navidrome)"},
        {"env": "AUTH_ENABLED", "value": "да" if config.AUTH_ENABLED else "нет", "help": ""},
    ]


def _write_secret_file(path: str, content: str, prefix: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=prefix, suffix=".tmp")   # mode 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------------------ Deezer ARL (секрет)
_ARL_RE = re.compile(r"^[0-9a-f]{100,400}$", re.IGNORECASE)


def deezer_status() -> dict:
    env = bool(config.DEEZER_ARL.strip())
    return {"source": "env" if env else ("file" if config.deezer_arl() else ""),
            "configured": bool(config.deezer_arl())}


def set_deezer_arl(arl: str) -> dict:
    if config.DEEZER_ARL.strip():
        raise ValueError("ARL задан через env DEEZER_ARL — он важнее UI. Уберите env, чтобы править здесь.")
    arl = arl.strip()
    if not _ARL_RE.match(arl):
        raise ValueError("ARL — длинная hex-строка (обычно 192 символа) из cookie «arl» на deezer.com")
    with _lock:
        _write_secret_file(config.DEEZER_ARL_FILE, arl + "\n", ".deezer_arl.")
    return deezer_status()


def clear_deezer_arl() -> dict:
    if config.DEEZER_ARL.strip():
        raise ValueError("ARL задан через env — удалить из UI нельзя.")
    with _lock:
        try:
            os.remove(config.DEEZER_ARL_FILE)
        except OSError:
            pass
    return deezer_status()


# ------------------------------------------------------------------ YouTube cookies
_COOKIES_MAX = 2 * 1024 * 1024


def _cookie_rows(text: str) -> List[str]:
    """Строки-cookies Netscape-файла (комментарии — кроме #HttpOnly_ — пропускаем)."""
    return [ln for ln in text.split("\n")
            if ln.strip() and (not ln.startswith("#") or ln.startswith("#HttpOnly_"))]


def cookies_status() -> dict:
    path = config.COOKIE_FILE
    if not os.path.exists(path):
        return {"present": False}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            rows = _cookie_rows(fh.read())
        return {"present": True, "modified": os.stat(path).st_mtime, "cookies": len(rows),
                "youtube": sum(1 for ln in rows if "youtube.com" in ln)}
    except OSError:
        return {"present": True}


def set_cookies(raw: bytes) -> dict:
    """Проверить и сохранить cookies.txt (формат Netscape, как экспортируют
    расширения «Get cookies.txt LOCALLY» и т.п.)."""
    if len(raw) > _COOKIES_MAX:
        raise ValueError("Файл слишком большой для cookies.txt")
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    rows = _cookie_rows(text)
    if not rows or not all(len(ln.split("\t")) >= 7 for ln in rows):
        raise ValueError("Это не cookies.txt в формате Netscape (строки из 7 полей через Tab)")
    if not any("youtube.com" in ln for ln in rows):
        raise ValueError("В файле нет cookies youtube.com — экспортируйте их, находясь на youtube.com")
    with _lock:
        _write_secret_file(config.COOKIE_FILE, text if text.endswith("\n") else text + "\n", ".cookies.")
    return cookies_status()


def clear_cookies() -> dict:
    with _lock:
        try:
            os.remove(config.COOKIE_FILE)
        except OSError:
            pass
    return cookies_status()


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
