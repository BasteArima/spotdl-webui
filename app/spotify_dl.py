"""Одноразовое скачивание ОДНОГО трека со Spotify через librespot (по треку на
процесс). Используется ручными кнопками «Spotify 320k» / массовой закачкой.
Для апгрейда тысяч треков см. app.spotify_worker (одна сессия на весь сеанс).

    python -m app.spotify_dl <spotify_url> <template> <format> <bitrate> [safe] [realtime] [min_bitrate]

Печатает `OK <path>` (или `OK SKIP <path>`) при успехе. Требует Premium для 320k.
"""
import os
import sys


def main() -> None:
    if len(sys.argv) < 5:
        print("usage: spotify_dl <spotify_url> <template> <format> <bitrate> [safe] [realtime] [min_bitrate]",
              file=sys.stderr)
        sys.exit(2)
    spotify_url, template, fmt, bitrate = sys.argv[1:5]
    safe = sys.argv[5] if len(sys.argv) > 5 else ""
    realtime = len(sys.argv) > 6 and sys.argv[6] == "1"
    try:
        min_bitrate = int(sys.argv[7]) if len(sys.argv) > 7 else 0
    except ValueError:
        min_bitrate = 0

    from app import config, librespot_dl, library

    creds = config.ZOTIFY_CREDENTIALS_FILE
    if not os.path.exists(creds):
        print(f"[error] нет {creds} — сгенерируй: python -m app.gen_zotify_creds {creds}",
              file=sys.stderr)
        sys.exit(4)

    library.init_spotify()
    session = librespot_dl.open_session(creds)
    try:
        status, value = librespot_dl.download_one(
            session, spotify_url, template, fmt, bitrate,
            safe=safe, realtime=realtime, min_bitrate=min_bitrate,
            log=lambda m: print(m, flush=True))
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    if status == "fail":
        print(f"[error] {value}", file=sys.stderr)
        sys.exit(5)
    print(f"OK SKIP {value}" if status == "skip" else f"OK {value}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
