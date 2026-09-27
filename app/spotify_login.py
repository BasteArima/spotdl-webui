"""Вход в Spotify для синка Liked Songs (spotdl-запрос `saved`).

У «Любимых треков» нет публичной ссылки — spotdl читает их через Web API от
имени пользователя (--user-auth). Здесь OAuth-вход: токен (с refresh-токеном)
ложится в /conf/.spotify-user-token.json, дальше spotdl обновляет его сам.

Нужно своё приложение на developer.spotify.com (Redirect URI
http://127.0.0.1:9900/, API — Web API); его client_id/secret задаются во вкладке
«Настройки» webui (/conf/spotify_app.json) или env SPOTIFY_CLIENT_ID/SECRET.

Обычно вход делается из UI («Настройки» → «Войти в Spotify»): веб строит ссылку
сам (authorize_url, без spotipy), а обмен code на токен запускает этим модулем
в подпроцессе — spotipy не грузится в долгоживущий веб-процесс:

    echo CODE | python -m app.spotify_login --exchange   → одна JSON-строка

Запасной вариант — интерактивно в консоли сервера:

    docker exec -it spotdl-webui python -m app.spotify_login
"""
import json
import os
import sys
from urllib.parse import parse_qs, urlencode, urlparse

from . import config

# Ровно те же redirect и scope, что у spotdl (spotdl/utils/spotify.py): spotipy
# считает кэш валидным, только если его scope покрывает запрошенный, — иначе
# spotdl потребует вход заново.
REDIRECT_URI = "http://127.0.0.1:9900/"
SCOPE = "user-library-read,user-follow-read,playlist-read-private"
_AUTHORIZE = "https://accounts.spotify.com/authorize"

_HINT_403 = ("403 обычно значит, что аккаунт не в allowlist приложения: используйте "
             "своё приложение (его владелец проходит всегда) или добавьте e-mail "
             "аккаунта в User Management приложения.")


def app_credentials() -> tuple:
    """(client_id, client_secret, свои_ли). Без своего — общий client_id spotdl
    (почти наверняка упрётся в allowlist, но пусть Spotify скажет это сам)."""
    cid, secret = config.spotify_app()
    if cid:
        return cid, secret, True
    from .library import _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET
    return _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET, False


def authorize_url(client_id: str, state: str = "") -> str:
    """Ссылка на страницу входа Spotify (как spotipy.get_authorize_url)."""
    params = {"client_id": client_id, "response_type": "code",
              "redirect_uri": REDIRECT_URI, "scope": SCOPE.replace(",", " ")}
    if state:
        params["state"] = state
    return _AUTHORIZE + "?" + urlencode(params)


def parse_redirect(raw: str) -> tuple:
    """Разобрать вставленный адрес 127.0.0.1:9900/?code=…&state=… (или голый
    code). Возвращает (code, state, error); error — если вход отклонён."""
    raw = (raw or "").strip()
    if not raw:
        return "", "", ""
    if "://" not in raw and "?" not in raw and "=" not in raw:
        return raw, "", ""          # вставили только code (не адрес)
    q = parse_qs(urlparse(raw).query)
    one = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    return one("code"), one("state"), one("error")


def _fix_owner(path: str) -> None:
    """docker exec по умолчанию идёт от root, а spotdl бежит под PUID:PGID и
    должен ПЕРЕЗАПИСЫВАТЬ кэш при обновлении токена — отдаём файл ему."""
    try:
        os.chmod(path, 0o600)   # внутри refresh-токен — секрет
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(path, int(os.environ.get("PUID", "998")),
                     int(os.environ.get("PGID", "100")))
    except (OSError, ValueError) as exc:
        print(f"[warn] не удалось выставить владельца/права {path}: {exc}", file=sys.stderr)


def exchange(code: str) -> dict:
    """Обменять code на токен, сохранить его и проверить доступ к Liked Songs.
    {"ok": True, "user", "total"} либо {"ok": False, "error"[, "hint"]}.
    При неудаче токен удаляется — полурабочий вход хуже, чем никакого."""
    from spotipy import Spotify, SpotifyException
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

    cid, secret, _own = app_credentials()
    path = config.SPOTIFY_USER_TOKEN_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    oauth = SpotifyOAuth(client_id=cid, client_secret=secret, redirect_uri=REDIRECT_URI,
                         scope=SCOPE, cache_handler=CacheFileHandler(cache_path=path),
                         open_browser=False)

    def fail(error: str, hint: str = "") -> dict:
        try:
            os.remove(path)
        except OSError:
            pass
        return {"ok": False, "error": error, "hint": hint}

    try:
        oauth.get_access_token(code, as_dict=False, check_cache=False)  # пишет в кэш
    except SpotifyOauthError as exc:
        # invalid_grant = code уже использован/протух или выдан другому приложению
        return fail(f"Spotify не принял code: {exc}",
                    "Начните вход заново — code одноразовый и живёт несколько минут.")
    if not os.path.exists(path):
        return fail("токен не сохранился")
    _fix_owner(path)

    sp = _client(oauth)
    try:
        me = sp.current_user() or {}
        total = (sp.current_user_saved_tracks(limit=1) or {}).get("total")
    except SpotifyException as exc:
        if exc.http_status == 429:
            # токен при этом рабочий — не удаляем, просто лимит приложения
            return {"ok": False, "error": rate_limit_message(exc)}
        return fail(f"Spotify отклонил запрос: {exc}",
                    _HINT_403 if exc.http_status == 403 else "")
    return {"ok": True, "user": me.get("display_name") or me.get("id") or "?", "total": total}


def _client(auth_manager):
    """spotipy без ожидания на 429. По умолчанию он честно спит Retry-After, а
    штраф Spotify для приложений в dev-режиме бывает на СУТКИ — задача висела бы
    сутки и держала очередь.

    Именно retries=0: urllib3 повторяет ответ с заголовком Retry-After (429/503)
    при ЛЮБОМ status_forcelist, если total > 0 — убрать 429 из списка мало
    (проверено). С нулём ответ сразу становится SpotifyException(429) с
    заголовками, откуда берём Retry-After для сообщения."""
    from spotipy import Spotify
    return Spotify(auth_manager=auth_manager, requests_timeout=20,
                   # не пустой: пустой spotipy заменяет своим списком, где есть 429
                   status_forcelist=(500, 502, 503, 504), retries=0, status_retries=0)


def rate_limit_message(exc) -> str:
    try:
        wait = int((getattr(exc, "headers", None) or {}).get("Retry-After", 0))
    except (TypeError, ValueError):
        wait = 0
    when = (f" Повтор возможен через ~{wait / 3600:.1f} ч." if wait >= 3600
            else f" Повтор возможен через ~{max(1, wait // 60)} мин." if wait else "")
    return "Spotify временно ограничил запросы вашего приложения (429)." + when


def _user_oauth():
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyOAuth
    cid, secret, _own = app_credentials()
    return SpotifyOAuth(client_id=cid, client_secret=secret, redirect_uri=REDIRECT_URI,
                        scope=SCOPE, open_browser=False,
                        cache_handler=CacheFileHandler(cache_path=config.SPOTIFY_USER_TOKEN_FILE))


def saved_track_urls() -> dict:
    """Ссылки на все треки Liked Songs от имени пользователя.

    Зачем самим, а не `spotdl sync saved --user-auth`: с --user-auth spotdl ходит
    ВСЁ через официальный API вашего приложения и на каждый трек дозапрашивает
    трек+исполнителя+альбом — ~3 запроса × сотни треков, и Spotify штрафует
    dev-приложение на сутки. Здесь — страницы по 50 (~17 запросов на 800 треков),
    а метаданные spotdl потом берёт своим обычным клиентом, как для плейлистов."""
    from spotipy import SpotifyException
    sp = _client(_user_oauth())
    urls = []
    try:
        page = sp.current_user_saved_tracks(limit=50)
        while page:
            for item in page.get("items") or []:
                track = (item or {}).get("track") or (item or {}).get("item") or {}
                if track.get("id") and not track.get("is_local"):
                    urls.append(f"https://open.spotify.com/track/{track['id']}")
            page = sp.next(page) if page.get("next") else None
    except SpotifyException as exc:
        if exc.http_status == 429:
            return {"ok": False, "error": rate_limit_message(exc)}
        return {"ok": False, "error": f"Spotify отклонил запрос: {exc}",
                "hint": _HINT_403 if exc.http_status == 403 else ""}
    return {"ok": True, "urls": urls}


def _interactive() -> None:
    cid, _secret, own = app_credentials()
    if not own:
        print("[warn] своё Spotify-приложение не настроено — пробую общий client_id spotdl.\n"
              "       Скорее всего Spotify ответит «User not registered in the Developer\n"
              "       Dashboard». Задайте приложение во вкладке «Настройки» webui.\n")
    print("\n=== Шаг 1 ===")
    print("Открой ссылку в браузере на ПК, войди в Spotify (можно через Facebook)")
    print("и разреши доступ:\n")
    print(authorize_url(cid))
    print("\n=== Шаг 2 ===")
    print(f"Браузер откроет {REDIRECT_URI}?code=... — страница НЕ загрузится, это нормально.")
    print("Скопируй весь адрес из адресной строки.\n")

    code, _state, error = parse_redirect(input("Вставь адрес (или только code) и нажми Enter: "))
    if error:
        print(f"[error] Spotify: {error}", file=sys.stderr)
        sys.exit(2)
    if not code:
        print("[error] пустой code", file=sys.stderr)
        sys.exit(2)

    print("\nПолучаю токен...")
    res = exchange(code)
    if not res["ok"]:
        print(f"[error] {res['error']}", file=sys.stderr)
        if res.get("hint"):
            print(f"        {res['hint']}", file=sys.stderr)
        sys.exit(1)
    print(f"\n✅ Готово: вошли как {res['user']}, в Liked Songs треков: {res['total']}.")
    print("Теперь добавьте в UI плейлист со ссылкой `saved` и нажмите Sync.")


def _exchange_cli() -> None:
    """Режим для веба: code из stdin (не в argv — его видно в ps), ответ —
    ОДНА JSON-строка последней строкой stdout."""
    code = sys.stdin.readline().strip()
    try:
        res = exchange(code) if code else {"ok": False, "error": "пустой code"}
    except Exception as exc:  # noqa: BLE001 — веб должен получить JSON, а не трейсбек
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # ASCII-JSON: веб разбирает вывод независимо от локали подпроцесса
    print(json.dumps(res), flush=True)
    sys.exit(0 if res.get("ok") else 1)


def _saved_urls_cli() -> None:
    """Режим для синка Liked Songs: ответ — ASCII-JSON последней строкой stdout."""
    try:
        res = saved_track_urls()
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(res), flush=True)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    if "--exchange" in sys.argv[1:]:
        _exchange_cli()
    if "--saved-urls" in sys.argv[1:]:
        _saved_urls_cli()
    try:
        _interactive()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
