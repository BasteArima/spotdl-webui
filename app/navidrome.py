"""Пересканирование Navidrome после изменений библиотеки.

Navidrome сам находит новые файлы только по своему расписанию. Здесь после синка,
загрузок и апгрейда библиотека помечается «изменённой» (notify_changed), а скан
запускается, когда всё затихло (maybe_scan из планировщика, раз в минуту):
  • нет активных задач и уже QUIET_SECONDS ничего не менялось — чтобы пакетная
    загрузка 300 треков дала ОДИН скан, а не 300;
  • не чаще min_interval минут — многонедельный апгрейд меняет по треку каждые
    несколько минут, и без потолка дёргал бы скан всё время.

Скан — Subsonic API `startScan` (пользователь Navidrome должен быть админом).
Авторизация токеном с солью (t = md5(пароль + s)), пароль по сети не ходит.
Без зависимостей — urllib из stdlib.

Подключение: env NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASSWORD (важнее UI,
как у остальных секретов) или вкладка «Настройки» → /conf/navidrome.json (600).
"""
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

_PATH = os.path.join(config.CONF_DIR, "navidrome.json")
_lock = threading.Lock()
QUIET_SECONDS = 60
_ENV = {"url": "NAVIDROME_URL", "user": "NAVIDROME_USER", "password": "NAVIDROME_PASSWORD"}

# состояние в памяти (после рестарта не нужно: скан — идемпотентная операция)
_state = {"pending": False, "changed_at": 0.0, "last_scan": 0.0,
          "last_result": None}   # {"ts", "ok", "message", "auto"}


# ------------------------------------------------------------------ конфиг
def _stored() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _env_locked() -> bool:
    return any(os.environ.get(v, "").strip() for v in _ENV.values())


def conf() -> dict:
    """Действующие параметры: env (если задан хоть один) важнее файла из UI."""
    s = _stored()
    if _env_locked():
        c = {k: os.environ.get(v, "").strip() for k, v in _ENV.items()}
    else:
        c = {k: str(s.get(k) or "").strip() for k in _ENV}
    c["url"] = c["url"].rstrip("/")
    c["enabled"] = bool(s.get("enabled", True))
    try:
        c["min_interval"] = max(1, min(1440, int(s.get("min_interval", 15))))
    except (TypeError, ValueError):
        c["min_interval"] = 15
    return c


def configured() -> bool:
    c = conf()
    return bool(c["url"] and c["user"] and c["password"])


def status() -> dict:
    """Для UI. Пароль наружу не отдаём."""
    c = conf()
    return {
        "source": "env" if _env_locked() else ("file" if c["url"] else ""),
        "url": c["url"], "user": c["user"], "has_password": bool(c["password"]),
        "configured": configured(), "enabled": c["enabled"], "min_interval": c["min_interval"],
        "pending": _state["pending"], "last_result": _state["last_result"],
    }


def save(url: str, user: str, password: str, enabled: bool, min_interval: int) -> dict:
    """Сохранить из UI. Пустой пароль = оставить сохранённый. Если адрес/логин
    заданы в env — из UI меняются только «включено» и интервал."""
    s = _stored()
    data = {"enabled": bool(enabled)}
    try:
        data["min_interval"] = max(1, min(1440, int(min_interval)))
    except (TypeError, ValueError):
        raise ValueError("Интервал — число минут от 1 до 1440") from None
    if not _env_locked():
        url = (url or "").strip().rstrip("/")
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("Адрес — вида http://192.168.1.10:4533 (с http:// и портом)")
        data.update(url=url, user=(user or "").strip(),
                    password=password if password else s.get("password", ""))
    d = os.path.dirname(_PATH) or "."
    os.makedirs(d, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".navidrome.", suffix=".tmp")  # mode 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, _PATH)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return status()


def clear() -> dict:
    if _env_locked():
        raise ValueError("Подключение задано через env NAVIDROME_* — удалить из UI нельзя.")
    with _lock:
        try:
            os.remove(_PATH)
        except OSError:
            pass
    _state.update(pending=False, last_result=None)
    return status()


# ------------------------------------------------------------------ Subsonic API
def _call(method: str, extra: dict = None) -> dict:
    """GET /rest/<method>; возвращает subsonic-response или бросает RuntimeError
    с понятным текстом."""
    c = conf()
    if not configured():
        raise RuntimeError("Navidrome не настроен (адрес, пользователь, пароль)")
    salt = secrets.token_hex(8)
    params = {"u": c["user"], "t": hashlib.md5((c["password"] + salt).encode("utf-8")).hexdigest(),
              "s": salt, "v": "1.16.1", "c": "spotdl-webui", "f": "json"}
    params.update(extra or {})
    url = f"{c['url']}/rest/{method}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} от {c['url']} — это точно адрес Navidrome?") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(f"нет связи с {c['url']}: {reason}. Из контейнера localhost — это "
                           f"сам webui; укажите IP сервера, например http://192.168.1.10:4533") from None
    except ValueError:
        raise RuntimeError(f"{c['url']} ответил не JSON — это точно адрес Navidrome?") from None
    r = body.get("subsonic-response") or {}
    if r.get("status") != "ok":
        err = r.get("error") or {}
        msg = err.get("message") or "ошибка"
        if err.get("code") == 40:
            msg = "неверный логин или пароль"
        elif err.get("code") == 50:
            msg = "нет прав — для скана нужен пользователь-администратор Navidrome"
        raise RuntimeError(f"Navidrome: {msg}")
    return r


def test() -> dict:
    """Проверка без запуска скана: логин (ping), права администратора (getUser →
    adminRole: startScan разрешён только админам) и состояние скана."""
    r = _call("ping")
    info = {"ok": True, "version": r.get("serverVersion") or r.get("version") or ""}
    try:
        user = _call("getUser", {"username": conf()["user"]}).get("user") or {}
        if user.get("adminRole") is False:
            return dict(info, ok=False, message="логин верный, но пользователь не администратор — "
                                                "Navidrome не даст ему запускать скан")
    except RuntimeError:
        pass            # getUser недоступен — права проверит сам скан
    try:
        st = _call("getScanStatus").get("scanStatus") or {}
        info.update(scanning=bool(st.get("scanning")), count=st.get("count"))
    except RuntimeError as e:
        info.update(ok=False, message=str(e))
    return info


def scan(auto: bool = False) -> dict:
    """Запустить скан. Возвращает и запоминает результат (для UI)."""
    try:
        _call("startScan")
        res = {"ok": True, "message": "скан запущен"}
    except RuntimeError as e:
        res = {"ok": False, "message": str(e)}
    res.update(ts=time.time(), auto=auto)
    _state.update(last_scan=res["ts"], last_result=res)
    if res["ok"]:
        _state["pending"] = False
    print(f"[navidrome] {'авто-' if auto else ''}скан: {res['message']}", flush=True)
    return res


# ------------------------------------------------------------------ авто-скан
def notify_changed() -> None:
    """Библиотека изменилась (синк/загрузка/апгрейд). Дёшево — только флаг."""
    if configured() and conf()["enabled"]:
        _state.update(pending=True, changed_at=time.time())


def maybe_scan(busy: bool) -> None:
    """Вызывается планировщиком раз в минуту. busy — есть ли активные задачи."""
    if not _state["pending"] or busy:
        return
    now = time.time()
    if now - _state["changed_at"] < QUIET_SECONDS:
        return
    c = conf()
    if not configured() or not c["enabled"]:
        _state["pending"] = False
        return
    if now - _state["last_scan"] < c["min_interval"] * 60:
        return          # потолок частоты; pending остаётся — скан будет позже
    res = scan(auto=True)
    if not res["ok"]:
        # недоступен — не долбим каждую минуту: следующая попытка через интервал
        _state["last_scan"] = now
