"""Генерация credentials.json для Zotify через OAuth — консольный вариант.
Обычно то же самое делается из UI («Настройки» → «Spotify напрямую»);
логика общая — app/zotify_login.py.

Подходит для аккаунтов, входящих через Facebook/Google/Apple (username/password
у них нет, и Spotify его всё равно блокирует для сторонних клиентов). Авторизация
идёт через обычную страницу входа Spotify, где есть «Continue with Facebook».

Запуск на сервере (headless), без проброса портов:

    docker exec -it spotdl-webui python -m app.gen_zotify_creds /conf/zotify_credentials.json

1) Скрипт печатает ссылку — открой её в браузере на своём ПК, войди (в т.ч. через
   Facebook).
2) После входа браузер откроет адрес http://127.0.0.1:5588/login?code=...
   Страница НЕ загрузится — это нормально. Скопируй из адресной строки весь адрес
   (или значение после `code=`) и вставь в терминал.
3) Скрипт сохранит credentials.json — дальше Zotify работает сам.
"""
import sys

from . import config, zotify_login
from .spotify_login import parse_redirect


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else config.ZOTIFY_CREDENTIALS_FILE
    config.ZOTIFY_CREDENTIALS_FILE = out     # exchange() пишет именно сюда

    verifier = zotify_login.new_verifier()
    print("\n=== Шаг 1 ===")
    print("Открой эту ссылку в браузере на ПК и войди в Spotify (можно через Facebook):\n")
    print(zotify_login.authorize_url(verifier))
    print("\n=== Шаг 2 ===")
    print(f"После входа браузер попробует открыть {zotify_login.REDIRECT_URI}?code=...")
    print("Страница НЕ загрузится — это нормально. Скопируй весь адрес (или значение после code=).\n")

    code, _state, error = parse_redirect(input("Вставь адрес (или code) и нажми Enter: "))
    if error or not code:
        print(f"[error] {error or 'пустой code'}", file=sys.stderr)
        sys.exit(2)

    print("\nПолучаю токен и сохраняю credentials...")
    res = zotify_login.exchange(code, verifier)
    if not res["ok"]:
        print(f"[error] {res['error']}", file=sys.stderr)
        if res.get("hint"):
            print(f"        {res['hint']}", file=sys.stderr)
        sys.exit(1)
    print(f"\n✅ Готово: {out}" + (f" (аккаунт {res['user']})" if res.get("user") else ""))
    print("Кнопка «Spotify 320k» в UI теперь будет работать.")


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
