"""Генерация credentials.json для Zotify через OAuth (вход в браузере).

Подходит для аккаунтов, входящих через Facebook/Google/Apple (username/password
у них нет, и Spotify его всё равно блокирует для сторонних клиентов). Авторизация
идёт через обычную страницу входа Spotify, где есть «Continue with Facebook».

Запуск на сервере (headless), без проброса портов:

    docker exec -it spotdl-webui python -m app.gen_zotify_creds /conf/zotify_credentials.json

1) Скрипт печатает ссылку — открой её в браузере на своём ПК, войди (в т.ч. через
   Facebook).
2) После входа браузер откроет адрес http://127.0.0.1:5588/login?code=...
   Страница НЕ загрузится — это нормально. Скопируй из адресной строки значение
   после `code=` (или весь URL) и вставь в терминал.
3) Скрипт сохранит credentials.json — дальше Zotify работает сам.
"""
import os
import sys
from urllib.parse import parse_qs, urlparse

_REDIRECT = "http://127.0.0.1:5588/login"


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "/conf/zotify_credentials.json"

    from librespot.core import Session
    from librespot.mercury import MercuryRequests
    from librespot.oauth import OAuth

    oauth = OAuth(MercuryRequests.keymaster_client_id, _REDIRECT, None)
    url = oauth.get_auth_url()

    print("\n=== Шаг 1 ===")
    print("Открой эту ссылку в браузере на ПК и войди в Spotify (можно через Facebook):\n")
    print(url)
    print("\n=== Шаг 2 ===")
    print("После входа браузер попробует открыть http://127.0.0.1:5588/login?code=...")
    print("Страница НЕ загрузится — это нормально. Скопируй значение после code= (или весь URL).\n")

    raw = input("Вставь code (или весь redirect-URL) и нажми Enter: ").strip()
    code = raw
    if "code=" in raw:
        code = parse_qs(urlparse(raw).query).get("code", [raw])[0]
    if not code:
        print("[error] пустой code", file=sys.stderr)
        sys.exit(2)

    print("\nПолучаю токен и сохраняю credentials...")
    oauth.set_code(code).request_token()
    creds = oauth.get_credentials()

    conf = (Session.Configuration.Builder()
            .set_store_credentials(True)
            .set_stored_credential_file(out)
            .build())
    builder = Session.Builder(conf)
    builder.login_credentials = creds

    try:
        session = builder.create()
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        # Сохранение reusable-кред происходит в момент аутентификации (до возможной
        # ошибки на последующих шагах). Если файл создан — считаем успехом.
        if not os.path.exists(out):
            print(f"[error] не удалось авторизоваться: {exc}", file=sys.stderr)
            sys.exit(1)

    if os.path.exists(out):
        print(f"\n✅ Готово: {out}")
        print("Кнопка «Zotify 320k» в UI теперь будет работать.")
    else:
        print("[error] credentials.json не создан", file=sys.stderr)
        sys.exit(1)


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
