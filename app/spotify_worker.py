"""Долгоживущий воркер скачивания со Spotify: ОДНА авторизация librespot на весь
сеанс апгрейда (вместо новой сессии на каждый трек — меньше нагрузки и меньше
«светит» аккаунт).

Протокол: фиксированные параметры — в argv; URL-ы трек-за-треком приходят из
stdin (по одному в строке); на каждый — JSON-результат в stdout; вся диагностика
— в stderr.

    python -m app.spotify_worker <template> <format> <bitrate> <realtime> <min_bitrate>

stdout (одна строка на трек): {"ok": bool, "skipped": bool, "status": "...", "msg": "..."}
"""
import json
import os
import sys


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> None:
    if len(sys.argv) < 6:
        print("usage: spotify_worker <template> <format> <bitrate> <realtime> <min_bitrate>",
              file=sys.stderr)
        sys.exit(2)
    template, fmt, bitrate, realtime_s, min_br_s = sys.argv[1:6]
    realtime = realtime_s == "1"
    try:
        min_bitrate = int(min_br_s)
    except ValueError:
        min_bitrate = 0

    from app import config, librespot_dl, library

    creds = config.ZOTIFY_CREDENTIALS_FILE
    if not os.path.exists(creds):
        _log(f"[worker][error] нет {creds}")
        sys.exit(4)

    library.init_spotify()
    session = librespot_dl.open_session(creds)
    _log("[worker] сессия librespot открыта (переиспользуется)")

    def run(url):
        return librespot_dl.download_one(
            session, url, template, fmt, bitrate,
            safe="", realtime=realtime, min_bitrate=min_bitrate, log=_log)

    for line in sys.stdin:
        url = line.strip()
        if not url:
            continue
        try:
            status, value = run(url)
        except Exception as exc:  # noqa: BLE001 — переоткрываем сессию и пробуем ещё раз
            _log(f"[worker] ошибка: {type(exc).__name__}: {exc} — переоткрываю сессию")
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                session = librespot_dl.open_session(creds)
                _log("[worker] сессия переоткрыта")
                status, value = run(url)
            except Exception as exc2:  # noqa: BLE001
                _log(f"[worker] повтор не удался: {type(exc2).__name__}: {exc2}")
                status, value = ("fail", str(exc2))
        sys.stdout.write(json.dumps({
            "ok": status == "ok", "skipped": status == "skip",
            "status": status, "msg": value,
        }) + "\n")
        sys.stdout.flush()

    _log("[worker] stdin закрыт — выход")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)
