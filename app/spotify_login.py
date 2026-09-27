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
import time
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
            _remember_ban(cid, exc)
            return {"ok": False, "error": rate_limit_message(exc)}
        return fail(f"Spotify отклонил запрос: {exc}",
                    _HINT_403 if exc.http_status == 403 else "")
    try:
        os.remove(_BAN_FILE)     # приложение отвечает — запомненный штраф снят
    except OSError:
        pass
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


def _when(wait: int) -> str:
    if not wait:
        return ""
    return (f" Повтор возможен через ~{wait / 3600:.1f} ч." if wait >= 3600
            else f" Повтор возможен через ~{max(1, wait // 60)} мин.")


def rate_limit_message(exc) -> str:
    try:
        wait = int((getattr(exc, "headers", None) or {}).get("Retry-After", 0))
    except (TypeError, ValueError):
        wait = 0
    return "Spotify временно ограничил запросы вашего приложения (429)." + _when(wait)


def _user_oauth():
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyOAuth
    cid, secret, _own = app_credentials()
    return SpotifyOAuth(client_id=cid, client_secret=secret, redirect_uri=REDIRECT_URI,
                        scope=SCOPE, open_browser=False,
                        cache_handler=CacheFileHandler(cache_path=config.SPOTIFY_USER_TOKEN_FILE))


# Штраф 429 запоминаем: пока он действует, любой запрос от приложения его
# только продлевает — автосинк раз в сутки попадал бы в тот же бан.
_BAN_FILE = os.path.join(config.CONF_DIR, ".spotify-app-ratelimit.json")


def _ban_left(client_id: str) -> int:
    """Сколько секунд ещё действует запомненный 429 для этого приложения."""
    try:
        with open(_BAN_FILE, encoding="utf-8") as fh:
            ban = json.load(fh)
    except (OSError, ValueError):
        return 0
    if ban.get("client_id") != client_id:
        return 0          # новое приложение — старый штраф к нему не относится
    return max(0, int(ban.get("until", 0) - time.time()))


def _remember_ban(client_id: str, exc) -> None:
    try:
        wait = int((getattr(exc, "headers", None) or {}).get("Retry-After", 0))
    except (TypeError, ValueError):
        wait = 0
    if wait <= 0:
        return
    try:
        with open(_BAN_FILE, "w", encoding="utf-8") as fh:
            json.dump({"client_id": client_id, "until": int(time.time()) + wait}, fh)
    except OSError:
        pass


def _song_dict(track: dict, disc_count: int) -> dict:
    """Трек в формате spotdl Song — те же поля, что в spotdl Song.from_url, но
    только из ответа «Любимых» (альбом там упрощённый). Все поля, по которым
    spotdl решает «дозапросить трек» (genres, disc_count, tracks_count,
    track_number, album_id, album_artist), заполнены: genres — пустой список, а
    не None, — иначе он снова пошёл бы в Spotify за каждым треком.

    Жанры/лейбл/копирайт требуют полных исполнителей и альбомов — пакетные
    запросы /artists и /albums dev-приложению дали 429 со штрафом ~сутки
    (дважды, на свежих приложениях), поэтому без них."""
    alb = track.get("album") or {}
    release = str(alb.get("release_date") or "")
    try:
        year = int(release[:4])
    except ValueError:
        year = 0
    images = [i for i in alb.get("images") or [] if i.get("url")]
    cover = max(images, key=lambda i: (i.get("width") or 0) * (i.get("height") or 0))["url"] if images else None
    artists = track.get("artists") or [{}]
    alb_artists = alb.get("artists") or artists
    return {
        "name": track["name"],
        "artists": [a.get("name") or "" for a in artists],
        "artist": artists[0].get("name") or "",
        "artist_id": artists[0].get("id"),
        "genres": [],
        "disc_number": track.get("disc_number") or 1,
        "disc_count": disc_count,
        "album_id": alb.get("id"),
        "album_name": alb.get("name") or "",
        "album_artist": alb_artists[0].get("name") or "",
        "album_type": alb.get("album_type"),
        "duration": int((track.get("duration_ms") or 0) / 1000),
        "year": year,
        "date": release,
        "track_number": track.get("track_number") or 1,
        "tracks_count": alb.get("total_tracks") or 1,
        "song_id": track["id"],
        "explicit": bool(track.get("explicit")),
        "publisher": "",
        "url": (track.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{track['id']}",
        "isrc": (track.get("external_ids") or {}).get("isrc"),
        "cover_url": cover,
        "copyright_text": None,
        "popularity": track.get("popularity"),
    }


def saved_songs() -> dict:
    """Все треки Liked Songs сразу в формате spotdl (для `spotdl download
    <файл>.spotdl`): только страницы «Любимых» по 50 — ~17 запросов на 800
    треков, с паузой между ними.

    Зачем: ссылки на треки spotdl разбирает ПО ОДНОЙ и последовательно
    (~15–25 с на трек: 806 треков — часы подготовки до первой загрузки). Из
    готового файла он качает сразу, не делая ни одного запроса за метаданными."""
    from spotipy import SpotifyException
    cid = app_credentials()[0]
    left = _ban_left(cid)
    if left:
        return {"ok": False, "error": "Spotify ещё держит ограничение 429 для этого приложения — "
                "запросы не отправлялись, чтобы не продлить его." + _when(left)}
    sp = _client(_user_oauth())
    tracks, page_no = [], 0
    try:
        page = sp.current_user_saved_tracks(limit=50)
        while page:
            page_no += 1
            for item in page.get("items") or []:
                t = (item or {}).get("track") or (item or {}).get("item") or {}
                if t.get("id") and t.get("name") and not t.get("is_local") and t.get("duration_ms"):
                    tracks.append(t)
            if not page.get("next"):
                break
            time.sleep(1)        # не частим: лимит у dev-приложений строгий
            page = sp.next(page)
    except SpotifyException as exc:
        where = f" (страница {page_no + 1} списка, получено треков: {len(tracks)})"
        if exc.http_status == 429:
            _remember_ban(cid, exc)
            return {"ok": False, "error": rate_limit_message(exc) + where}
        return {"ok": False, "error": f"Spotify отклонил запрос{where}: {exc}",
                "hint": _HINT_403 if exc.http_status == 403 else ""}

    # число дисков альбома — по лайкнутым трекам (полный альбом не запрашиваем)
    discs = {}
    for t in tracks:
        aid = (t.get("album") or {}).get("id")
        discs[aid] = max(discs.get(aid, 1), int(t.get("disc_number") or 1))
    songs = [_song_dict(t, discs.get((t.get("album") or {}).get("id"), 1)) for t in tracks]
    return {"ok": True, "songs": songs}


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


def _saved_songs_cli(out_path: str) -> None:
    """Режим для синка Liked Songs: треки пишутся в out_path (список в формате
    spotdl), в stdout — ASCII-JSON {"ok", "count"} последней строкой."""
    try:
        res = saved_songs()
        if res.get("ok"):
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(res["songs"], fh, ensure_ascii=False)
            os.replace(tmp, out_path)
            res = {"ok": True, "count": len(res["songs"])}
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(res), flush=True)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    if "--exchange" in sys.argv[1:]:
        _exchange_cli()
    if "--saved-songs" in sys.argv[1:]:
        _saved_songs_cli(sys.argv[sys.argv.index("--saved-songs") + 1])
    try:
        _interactive()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
