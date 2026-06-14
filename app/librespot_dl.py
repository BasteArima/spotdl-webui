"""Общее ядро скачивания трека НАПРЯМУЮ со Spotify через librespot.

Используется и одноразовым CLI (app.spotify_dl, по треку на процесс), и
долгоживущим воркером (app.spotify_worker, одна авторизация на весь апгрейд).
Сессия передаётся снаружи и ПЕРЕИСПОЛЬЗУЕТСЯ (здесь не закрывается)."""
import os
import shutil
import tempfile
import time


def track_id_from_url(url: str) -> str:
    # https://open.spotify.com/track/<id>?si=...  ->  <id>
    tail = url.rstrip("/").split("/track/")[-1]
    return tail.split("?")[0].split("/")[0]


def open_session(creds_path: str):
    """Открыть librespot-сессию по credentials.json."""
    from librespot.core import Session
    conf = Session.Configuration.Builder().set_store_credentials(False).build()
    return Session.Builder(conf).stored_file(creds_path).create()


def _pace_sleep(written: int, size, duration, started_at: float, now: float) -> float:
    """Сколько подождать, чтобы скачивание шло со скоростью прослушивания (анти-бан)."""
    if not size or not duration:
        return 0.0
    expected = (written / size) * duration
    return max(0.0, expected - (now - started_at))


def download_one(session, spotify_url: str, template: str, fmt: str, bitrate: str,
                 safe: str = "", realtime: bool = False, min_bitrate: int = 0, log=print):
    """Скачать один трек через ПЕРЕДАННУЮ сессию и положить в библиотеку.
    Возвращает (status, value): status ∈ {'ok','skip','fail'}; value — путь или текст.
    SpotifyClient должен быть уже инициализирован вызывающим (library.init_spotify)."""
    from app import config, library
    from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
    from librespot.metadata import TrackId

    song = library.fetch_song(spotify_url)

    # Пропуск уже-хороших файлов (порог задаёт только апгрейд)
    if min_bitrate > 0:
        out_existing = library.target_path(song, template, fmt)
        if os.path.exists(out_existing):
            br = library.file_bitrate(str(out_existing))
            if br and br >= min_bitrate:
                log(f"[skip] уже {br}k: {out_existing}")
                return ("skip", str(out_existing))

    uri = f"spotify:track:{track_id_from_url(spotify_url)}"
    log(f"[librespot] {uri}: аудио ({'real-time' if realtime else 'полная'} скорость)")
    track_id = TrackId.from_uri(uri)
    stream = session.content_feeder().load(
        track_id, VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH), False, None)

    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    tmpdir = tempfile.mkdtemp(dir=config.UPLOADS_DIR, prefix="lr_")
    ogg = os.path.join(tmpdir, "track.ogg")
    total = 0
    duration = float(getattr(song, "duration", 0) or 0)
    try:
        inp = stream.input_stream.stream()
        size = None
        try:
            size = inp.size()
        except Exception:  # noqa: BLE001
            size = None
        if realtime:
            if not duration:
                log("[warn] REAL-TIME отключён: нет длительности — полная скорость (анти-бан не действует)")
            elif not size:
                size = int(duration * 40000)
                log(f"[warn] real-time по оценке размера (~{size} байт за {int(duration)}с)")
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
                        time.sleep(s)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    if total < 10000:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return ("fail", "поток пуст/слишком мал — трек недоступен?")

    m3u = config.m3u_path(safe) if safe else None
    try:
        out = library.place_file(ogg, song, template, fmt, bitrate, log=log, m3u_path=m3u)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return ("ok", out)
