"""Скачивание трека НАПРЯМУЮ со Spotify через librespot (без api.spotify.com).

    python -m app.spotify_dl <spotify_track_url> <output_template> <format> <bitrate>

Старый Zotify брал метаданные через публичный api.spotify.com/v1/tracks и упирался
в 429 (rate limit на токен keymaster). Здесь метаданные НЕ нужны от librespot —
мы тянем только аудио по track-id через аудио-серверы Spotify (content_feeder),
а метаданные/обложку/путь даёт общий app.library через spotdl. Так 429 от
метаданных исключён.

Креды: /conf/zotify_credentials.json (создаются `app.gen_zotify_creds`).
Печатает `OK <path>` при успехе. Требует Premium для 320k.
"""
import os
import shutil
import sys
import tempfile
import time


def _track_id_from_url(url: str) -> str:
    # https://open.spotify.com/track/<id>?si=...  ->  <id>
    tail = url.rstrip("/").split("/track/")[-1]
    return tail.split("?")[0].split("/")[0]


def _pace_sleep(written: int, size, duration, started_at: float, now: float) -> float:
    """Сколько секунд подождать, чтобы скачивание шло со скоростью прослушивания.
    Цель: скачать `size` байт примерно за `duration` секунд (real-time, анти-бан)."""
    if not size or not duration:
        return 0.0
    expected = (written / size) * duration
    return max(0.0, expected - (now - started_at))


def main() -> None:
    if len(sys.argv) < 5:
        print("usage: spotify_dl <spotify_url> <template> <format> <bitrate> [safe] [realtime]",
              file=sys.stderr)
        sys.exit(2)
    spotify_url, template, fmt, bitrate = sys.argv[1:5]
    safe = sys.argv[5] if len(sys.argv) > 5 else ""
    realtime = len(sys.argv) > 6 and sys.argv[6] == "1"

    from app import config, library

    creds = config.ZOTIFY_CREDENTIALS_FILE
    if not os.path.exists(creds):
        print(f"[error] нет {creds} — сгенерируй: python -m app.gen_zotify_creds {creds}",
              file=sys.stderr)
        sys.exit(4)

    # Метаданные сначала: нужны для пути, тегов и проверки «уже хорошего качества».
    library.init_spotify()
    song = library.fetch_song(spotify_url)

    # Апгрейд: если файл уже есть и его битрейт >= порога — не перекачиваем.
    min_br = 0
    try:
        min_br = int(os.environ.get("UPGRADE_MIN_BITRATE", "0") or "0")
    except ValueError:
        min_br = 0
    if min_br > 0:
        out_existing = library.target_path(song, template, fmt)
        if os.path.exists(out_existing):
            br = library.file_bitrate(str(out_existing))
            if br and br >= min_br:
                print(f"OK SKIP {out_existing} (уже {br}k)", flush=True)
                return

    from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
    from librespot.core import Session
    from librespot.metadata import TrackId

    tid = _track_id_from_url(spotify_url)
    uri = f"spotify:track:{tid}"

    print("[librespot] открываю сессию по credentials.json", flush=True)
    conf = Session.Configuration.Builder().set_store_credentials(False).build()
    session = Session.Builder(conf).stored_file(creds).create()

    mode = "real-time" if realtime else "обычная"
    print(f"[librespot] {uri}: запрашиваю аудио (quality=VERY_HIGH, {mode} скорость)", flush=True)
    track_id = TrackId.from_uri(uri)
    stream = session.content_feeder().load(
        track_id, VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH), False, None)

    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    tmpdir = tempfile.mkdtemp(dir=config.UPLOADS_DIR, prefix="lr_")
    ogg = os.path.join(tmpdir, "track.ogg")
    total = 0
    duration = float(getattr(song, "duration", 0) or 0)  # секунды
    try:
        inp = stream.input_stream.stream()
        size = None
        try:
            size = inp.size()
        except Exception:  # noqa: BLE001
            size = None
        started = time.time()
        with open(ogg, "wb") as fh:
            while True:
                chunk = inp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
                if realtime:
                    s = _pace_sleep(total, size, duration, started, time.time())
                    if s > 0:
                        time.sleep(min(s, 5.0))
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    print(f"[librespot] получено {total} байт", flush=True)
    if total < 10000:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("[error] поток пуст/слишком мал — трек недоступен?", file=sys.stderr)
        sys.exit(5)

    m3u = config.m3u_path(safe) if safe else None
    try:
        out = library.place_file(ogg, song, template, fmt, bitrate,
                                 log=lambda m: print(m, flush=True), m3u_path=m3u)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
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
