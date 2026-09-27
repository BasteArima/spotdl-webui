"""Авторизация webui.

Режимы (по приоритету):
  • AUTH_ENABLED=false — вход отключён целиком (сервис только в локалке, пароль
    лишний). Все API открыты, экран входа не показывается.
  • пароль из env APP_PASSWORD (старое имя APP_AUTH_TOKEN тоже работает) —
    важнее UI, сменить его из UI нельзя;
  • иначе пароль задаётся в UI: при первом открытии сервис просит его придумать
    (first-run setup), на сервере хранится только хэш (PBKDF2) в
    /conf/.webui-auth.json. Забыли — удалите этот файл, сервис попросит задать
    пароль заново.

Сессии: после входа браузер получает подписанный токен `v1.<exp>.<sig>` (HMAC
от секрета из /conf/.webui-auth.json), а НЕ хранит пароль. Проверка токена —
один HMAC, без PBKDF2 на каждый опрос /api/jobs. В ключ подписи подмешан отпечаток
пароля, поэтому смена пароля (в UI или в env) разлогинивает все старые сессии.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time

from . import config

_PATH = os.path.join(config.CONF_DIR, ".webui-auth.json")
_lock = threading.Lock()
_cache = None

SESSION_TTL = 30 * 24 * 3600          # сессия живёт 30 дней
MIN_PASSWORD_LEN = 6
_PBKDF2_ITERS = 240_000

# защита от перебора: столько неудач с одного IP за окно → пауза
_FAIL_LIMIT = 5
_FAIL_WINDOW = 10 * 60
_failures: dict = {}


# ------------------------------------------------------------------ хранилище
def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save(data: dict) -> None:
    global _cache
    d = os.path.dirname(_PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".webui-auth.", suffix=".tmp")  # mode 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    _cache = data


def _secret() -> bytes:
    """Секрет подписи сессий; создаётся при первой необходимости."""
    with _lock:
        data = dict(_load())
        if not data.get("secret"):
            data["secret"] = secrets.token_hex(32)
            _save(data)
        return bytes.fromhex(data["secret"])


# ------------------------------------------------------------------ режимы
def enabled() -> bool:
    return config.AUTH_ENABLED


def env_password() -> str:
    return config.APP_PASSWORD


def source() -> str:
    """env | ui | "" (пароль ещё не задан)."""
    if env_password():
        return "env"
    return "ui" if _load().get("pw_hash") else ""


def setup_required() -> bool:
    return enabled() and not source()


# ------------------------------------------------------------------ пароль
def _hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def _verify_hash(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def check_password(password: str) -> bool:
    if env_password():
        return hmac.compare_digest(password.encode("utf-8"), env_password().encode("utf-8"))
    stored = _load().get("pw_hash")
    return bool(stored) and _verify_hash(password, stored)


def _validate_new(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Пароль — минимум {MIN_PASSWORD_LEN} символов")


def set_password(password: str) -> None:
    """Задать/сменить пароль, хранимый в UI. Старые сессии перестают работать
    сами: в ключ подписи входит хэш пароля."""
    if env_password():
        raise ValueError("Пароль задан через env APP_PASSWORD/APP_AUTH_TOKEN — сменить его "
                         "можно только там.")
    _validate_new(password)
    with _lock:
        data = dict(_load())
        data["pw_hash"] = _hash(password, secrets.token_bytes(16))
        _save(data)


def setup(password: str) -> str:
    """Первичная установка пароля. Возвращает токен сессии."""
    if not setup_required():
        raise PermissionError("Пароль уже задан")
    set_password(password)
    return issue_token()


# ------------------------------------------------------------------ сессии
def _signing_key() -> bytes:
    """Ключ = секрет + отпечаток ТЕКУЩЕГО пароля: смена пароля (в UI или env)
    мгновенно инвалидирует все выданные ранее токены."""
    marker = (hashlib.sha256(env_password().encode("utf-8")).hexdigest()
              if env_password() else _load().get("pw_hash", ""))
    return hmac.new(_secret(), b"session|" + marker.encode("utf-8"), hashlib.sha256).digest()


def _sign(payload: str) -> str:
    sig = hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def issue_token() -> str:
    payload = f"v1.{int(time.time()) + SESSION_TTL}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> bool:
    if not token:
        return False
    # совместимость: сырой пароль из env в заголовке (старые клиенты/скрипты).
    # Только для env — сравнение дешёвое; хэш из UI на каждый запрос не гоняем.
    if env_password() and hmac.compare_digest(token.encode("utf-8"),
                                              env_password().encode("utf-8")):
        return True
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    try:
        exp = int(parts[1])
    except ValueError:
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(_sign(f"{parts[0]}.{parts[1]}"), parts[2])


def is_authorized(token: str) -> bool:
    if not enabled():
        return True
    if setup_required():
        return False
    return verify_token(token)


# ------------------------------------------------------------------ анти-перебор
def throttled(ip: str) -> int:
    """Сколько секунд ещё ждать этому IP (0 — можно пробовать)."""
    now = time.time()
    fails = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW]
    _failures[ip] = fails
    if len(fails) >= _FAIL_LIMIT:
        return max(1, int(_FAIL_WINDOW - (now - fails[0])))
    return 0


def register_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())
    if len(_failures) > 1000:            # не копить память от сканеров
        _failures.clear()


def register_success(ip: str) -> None:
    _failures.pop(ip, None)
