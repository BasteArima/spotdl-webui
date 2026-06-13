"""Скачивание трека РЕАЛЬНО со Spotify через Zotify (librespot) и размещение в
библиотеке. С Premium-аккаунтом — 320k. Источник для треков, которых нет на
YouTube, и для апгрейда качества.

    python -m app.zotify_dl <spotify_track_url> <output_template> <format> <bitrate>

Zotify качает нативный Ogg (без перекодирования) во временную папку, затем общий
app.library конвертирует в формат библиотеки, кладёт по шаблону и вшивает
метаданные/обложку со Spotify. Печатает `OK <path>` при успехе.

ВНИМАНИЕ: использование librespot нарушает ToS Spotify и может привести к бану
аккаунта. ARL/креды — секрет, в образ/репозиторий не попадают.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

# Запас по времени: real-time-загрузка одного трека ~ длительность трека + авторизация.
_TIMEOUT = int(os.environ.get("ZOTIFY_TIMEOUT", "900"))


def _find_audio(root: str):
    for ext in ("ogg", "mp3", "m4a", "opus", "flac"):
        hits = glob.glob(os.path.join(root, f"**/*.{ext}"), recursive=True)
        if hits:
            return hits[0]
    return None


def main() -> None:
    if len(sys.argv) < 5:
        print("usage: zotify_dl <spotify_url> <template> <format> <bitrate>", file=sys.stderr)
        sys.exit(2)
    spotify_url, template, fmt, bitrate = sys.argv[1:5]

    from app import config, library

    if not config.zotify_configured():
        print("[error] Zotify не настроен: нужен /conf/zotify_credentials.json или "
              "ZOTIFY_USERNAME/ZOTIFY_PASSWORD", file=sys.stderr)
        sys.exit(4)

    tmp = tempfile.mkdtemp(dir=config.UPLOADS_DIR, prefix="zot_")
    creds = config.ZOTIFY_CREDENTIALS_FILE
    cmd = [
        sys.executable, "-m", "zotify", "--no-splash",
        "--root-path", tmp,
        "--output", "track.{ext}",
        "--download-format", "ogg",                 # copy — нативный Ogg, без потерь
        "--download-quality", config.ZOTIFY_QUALITY,
        "--download-real-time", "True" if config.ZOTIFY_REAL_TIME else "False",
        "--skip-existing", "False",
        "--print-splash", "False",
    ]
    if os.path.exists(creds):
        cmd += ["--credentials-location", creds]
    else:
        cmd += ["--credentials-location", creds, "--save-credentials", "True",
                "--username", config.ZOTIFY_USERNAME, "--password", config.ZOTIFY_PASSWORD]
    cmd += [spotify_url]

    print(f"[zotify] запуск (quality={config.ZOTIFY_QUALITY}, real_time={config.ZOTIFY_REAL_TIME})",
          flush=True)
    try:
        rc = subprocess.run(cmd, timeout=_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        print("[error] Zotify превысил таймаут (возможно, требует интерактивного входа)",
              file=sys.stderr)
        sys.exit(6)

    src = _find_audio(tmp)
    if rc != 0 or not src:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[error] Zotify не скачал файл (код {rc})", file=sys.stderr)
        sys.exit(5)
    print(f"[zotify] скачано: {os.path.basename(src)}", flush=True)

    library.init_spotify()
    song = library.fetch_song(spotify_url)
    try:
        out = library.place_file(src, song, template, fmt, bitrate,
                                 log=lambda m: print(m, flush=True))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK {out}", flush=True)


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
