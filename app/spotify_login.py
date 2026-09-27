"""Одноразовый вход в Spotify для синка Liked Songs (spotdl-запрос `saved`).

У «Любимых треков» нет публичной ссылки — spotdl читает их через Web API от
имени пользователя (--user-auth). Этот скрипт проводит OAuth-вход и кладёт токен
(с refresh-токеном) в /conf/.spotify-user-token.json. Дальше spotdl обновляет
его сам, повторный вход не нужен, пока доступ не отозван в аккаунте Spotify.

Нужно своё приложение на developer.spotify.com (Redirect URI
http://127.0.0.1:9900/, API — Web API); его client_id/secret — в env
SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET или файлом /conf/spotify_app.json.

Запуск на сервере (headless), без проброса портов:

    docker exec -it spotdl-webui python -m app.spotify_login

1) Скрипт печатает ссылку — открой её в браузере на ПК, войди (можно через
   Facebook) и разреши доступ.
2) Браузер откроет http://127.0.0.1:9900/?code=... — страница НЕ загрузится,
   это нормально. Скопируй весь адрес из адресной строки и вставь в терминал.
"""
import os
import sys

from . import config

# Ровно те же redirect и scope, что у spotdl (spotdl/utils/spotify.py): spotipy
# считает кэш валидным, только если его scope покрывает запрошенный, — иначе
# spotdl потребует вход заново.
REDIRECT_URI = "http://127.0.0.1:9900/"
SCOPE = "user-library-read,user-follow-read,playlist-read-private"


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


def main() -> None:
    from spotipy import Spotify, SpotifyException
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyOAuth

    client_id, client_secret = config.spotify_app()
    if not client_id:
        from .library import _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET
        client_id, client_secret = _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET
        print("[warn] своё Spotify-приложение не настроено — пробую общий client_id spotdl.\n"
              "       Скорее всего Spotify ответит «User not registered in the Developer\n"
              "       Dashboard». Тогда создайте приложение (см. README, «Liked Songs»).\n")

    path = config.SPOTIFY_USER_TOKEN_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_handler=CacheFileHandler(cache_path=path),
        open_browser=False,
    )

    print("\n=== Шаг 1 ===")
    print("Открой ссылку в браузере на ПК, войди в Spotify (можно через Facebook)")
    print("и разреши доступ:\n")
    print(oauth.get_authorize_url())
    print("\n=== Шаг 2 ===")
    print(f"Браузер откроет {REDIRECT_URI}?code=... — страница НЕ загрузится, это нормально.")
    print("Скопируй весь адрес из адресной строки.\n")

    raw = input("Вставь адрес (или только code) и нажми Enter: ").strip()
    code = oauth.parse_response_code(raw) if raw else ""
    if not code:
        print("[error] пустой code", file=sys.stderr)
        sys.exit(2)

    print("\nПолучаю токен...")
    oauth.get_access_token(code, as_dict=False, check_cache=False)  # пишет в кэш
    if not os.path.exists(path):
        print("[error] токен не сохранился", file=sys.stderr)
        sys.exit(1)
    _fix_owner(path)

    # проверка: токен рабочий и Liked Songs читаются
    sp = Spotify(auth_manager=oauth)
    try:
        me = sp.current_user() or {}
        total = (sp.current_user_saved_tracks(limit=1) or {}).get("total")
    except SpotifyException as exc:
        print(f"[error] Spotify отклонил запрос: {exc}", file=sys.stderr)
        if exc.http_status == 403:
            print("        403 обычно значит, что аккаунт не в allowlist приложения:\n"
                  "        используйте своё приложение (его владелец проходит всегда)\n"
                  "        или добавьте e-mail аккаунта в User Management приложения.",
                  file=sys.stderr)
        os.remove(path)
        sys.exit(1)

    who = me.get("display_name") or me.get("id") or "?"
    print(f"\n✅ Готово: вошли как {who}, в Liked Songs треков: {total}.")
    print(f"Токен: {path}")
    print("Теперь добавьте в UI плейлист со ссылкой `saved` и нажмите Sync.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
