"""Вход librespot (Zotify) через OAuth — для кнопки «Spotify 320k» и апгрейда.

Результат — переиспользуемые креды /conf/zotify_credentials.json. Подходит и
для аккаунтов с входом через Facebook/Google/Apple (у них нет пароля Spotify).

Обычно вход делается из UI («Настройки» → «Spotify напрямую»): веб сам строит
ссылку входа и PKCE-секрет (authorize_url/new_verifier — без librespot), а
обмен code на креды запускает этим модулем в ПОДПРОЦЕССЕ — librespot с его
protobuf не грузится в долгоживущий веб-процесс:

    echo '{"code": "...", "verifier": "..."}' | python -m app.zotify_login --exchange

Запасной вариант — консольный app.gen_zotify_creds (та же схема).
"""
import base64
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import quote

from . import config

# client_id «keymaster» (официальный клиент Spotify) и его redirect —
# ровно как в librespot (MercuryRequests.keymaster_client_id) и gen_zotify_creds.
CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
REDIRECT_URI = "http://127.0.0.1:5588/login"
# scope — копия списка из librespot.oauth.OAuth (там он приватный). Без
# «streaming» и компании librespot не получит доступ к аудио.
SCOPES = [
    "app-remote-control", "playlist-modify", "playlist-modify-private",
    "playlist-modify-public", "playlist-read", "playlist-read-collaborative",
    "playlist-read-private", "streaming", "ugc-image-upload", "user-follow-modify",
    "user-follow-read", "user-library-modify", "user-library-read", "user-modify",
    "user-modify-playback-state", "user-modify-private", "user-personalized",
    "user-read-birthdate", "user-read-currently-playing", "user-read-email",
    "user-read-play-history", "user-read-playback-position", "user-read-playback-state",
    "user-read-private", "user-read-recently-played", "user-top-read",
]
_TOKEN_URL = "https://accounts.spotify.com/api/token"


def new_verifier() -> str:
    """PKCE code_verifier (43–128 символов из [A-Za-z0-9-._~])."""
    return secrets.token_urlsafe(96)[:128]


def authorize_url(verifier: str, state: str = "") -> str:
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    url = ("https://accounts.spotify.com/authorize?response_type=code"
           f"&client_id={CLIENT_ID}&redirect_uri={quote(REDIRECT_URI, safe='')}"
           f"&code_challenge={challenge}&code_challenge_method=S256"
           f"&scope={'+'.join(SCOPES)}")
    return url + (f"&state={quote(state, safe='')}" if state else "")


def status() -> dict:
    """Для UI: есть ли креды и под кем (username читаем из json без librespot)."""
    path = config.ZOTIFY_CREDENTIALS_FILE
    if os.path.exists(path):
        user = ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = str(json.load(fh).get("username") or "")
        except (OSError, ValueError, AttributeError):
            pass
        return {"configured": True, "source": "file", "user": user}
    if config.ZOTIFY_USERNAME.strip() and config.ZOTIFY_PASSWORD.strip():
        return {"configured": True, "source": "env", "user": config.ZOTIFY_USERNAME.strip()}
    return {"configured": False, "source": "", "user": ""}


def logout() -> dict:
    try:
        os.remove(config.ZOTIFY_CREDENTIALS_FILE)
    except OSError:
        pass
    return status()


def exchange(code: str, verifier: str) -> dict:
    """Обменять code на токен и сохранить переиспользуемые креды librespot.
    {"ok": True, "user"} или {"ok": False, "error"[, "hint"]}. Пишем во временный
    файл и подменяем только при успехе — неудачный вход не портит рабочие креды."""
    import requests
    from librespot.core import Session
    from librespot.oauth import OAuth

    resp = requests.post(_TOKEN_URL, data={
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI, "code": code, "code_verifier": verifier,
    }, timeout=30)
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error_description") or resp.json().get("error") or ""
        except ValueError:
            pass
        return {"ok": False, "error": f"Spotify не принял code ({resp.status_code} {detail})".strip(),
                "hint": "Начните вход заново — code одноразовый и живёт несколько минут."}

    # публичный метод librespot: токен → LoginCredentials
    creds = OAuth(CLIENT_ID, REDIRECT_URI, None).ingest_token_response(resp.json()).get_credentials()

    out = config.ZOTIFY_CREDENTIALS_FILE
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".new"
    try:
        os.remove(tmp)
    except OSError:
        pass
    conf = (Session.Configuration.Builder()
            .set_store_credentials(True)
            .set_stored_credential_file(tmp)
            .build())
    builder = Session.Builder(conf)
    builder.login_credentials = creds
    user, error = "", ""
    try:
        session = builder.create()
        try:
            user = session.username()
        except Exception:  # noqa: BLE001
            pass
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        # креды сохраняются в момент аутентификации — ошибка на следующих шагах
        # (как и в gen_zotify_creds) не значит, что вход не удался
        error = f"{type(exc).__name__}: {exc}"
    if not os.path.exists(tmp):
        return {"ok": False, "error": "librespot не авторизовался" + (f": {error}" if error else "")}
    os.chmod(tmp, 0o600)   # переиспользуемые креды аккаунта — секрет
    os.replace(tmp, out)
    if not user:
        user = status()["user"]
    return {"ok": True, "user": user}


def _exchange_cli() -> None:
    """Режим для веба: JSON {code, verifier} из stdin, ответ — ASCII-JSON
    последней строкой stdout."""
    try:
        req = json.loads(sys.stdin.readline() or "{}")
        code, verifier = str(req.get("code") or ""), str(req.get("verifier") or "")
        res = exchange(code, verifier) if code and verifier else {"ok": False, "error": "нет code"}
    except Exception as exc:  # noqa: BLE001 — веб должен получить JSON, а не трейсбек
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(res), flush=True)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__" and "--exchange" in sys.argv[1:]:
    _exchange_cli()
