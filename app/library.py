"""Общая логика размещения аудиофайла в библиотеке с метаданными со Spotify.

Используется и заливкой локального файла (place_localfile.py), и Deezer-фолбэком
(deezer_dl.py). Делает то же, что spotdl после скачивания: путь по шаблону →
ffmpeg-конвертация в формат библиотеки → вшивание тегов и обложки.

Импорты spotdl лежат внутри функций — модуль грузится дёшево, тяжесть только при
реальном вызове (а вызываемся мы из отдельного процесса)."""
from pathlib import Path

# Публичные client_id/secret spotdl по умолчанию (spotdl/utils/config.py).
_DEFAULT_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
_DEFAULT_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"


def init_spotify() -> None:
    from spotdl.utils.spotify import SpotifyClient
    SpotifyClient.init(
        client_id=_DEFAULT_CLIENT_ID,
        client_secret=_DEFAULT_CLIENT_SECRET,
        no_cache=True,
    )


def fetch_song(spotify_url: str):
    from spotdl.types.song import Song
    return Song.from_url(spotify_url)


def target_path(song, template: str, fmt: str):
    """Абсолютный путь, куда ляжет трек (тот же, что использует place_file)."""
    from spotdl.utils.formatter import create_file_name
    out = create_file_name(song, template, fmt)
    if not out.is_absolute():
        out = Path("/") / out
    return out


def file_bitrate(path: str):
    """Битрейт аудиофайла в kbps (или None). Через mutagen, иначе ffprobe."""
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(path)
        if mf is not None and getattr(mf, "info", None) and getattr(mf.info, "bitrate", None):
            return int(mf.info.bitrate / 1000)
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=bit_rate",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=20)
        val = (r.stdout or "").strip()
        if val.isdigit():
            return int(int(val) / 1000)
    except Exception:  # noqa: BLE001
        pass
    return None


def append_to_m3u(out_path: str, song, m3u_path: str, template: str, fmt: str, log=print) -> None:
    """Дописать один трек в m3u-плейлист — без полного spotdl sync.
    Строку #EXTINF берём из той же функции spotdl (формат совпадает), а путь —
    реальный абсолютный путь записанного файла (out_path). Дубли не добавляем."""
    import os

    from spotdl.utils.m3u import create_m3u_content

    extinf = ""
    try:
        content = create_m3u_content([song], template, fmt)
        for line in content.splitlines():
            if line.startswith("#EXTINF"):
                extinf = line
                break
    except Exception:  # noqa: BLE001 — без #EXTINF тоже валидно
        extinf = ""

    existing = ""
    if os.path.exists(m3u_path):
        with open(m3u_path, "r", encoding="utf-8", errors="replace") as fh:
            existing = fh.read()
    else:
        os.makedirs(os.path.dirname(m3u_path) or ".", exist_ok=True)
        existing = "#EXTM3U\n"

    # уже в плейлисте — ничего не делаем
    if out_path in existing.splitlines():
        log(f"[m3u] трек уже в плейлисте — пропускаю")
        return

    if not existing.endswith("\n"):
        existing += "\n"
    block = (extinf + "\n" if extinf else "") + out_path + "\n"
    with open(m3u_path, "w", encoding="utf-8") as fh:
        fh.write(existing + block)
    log(f"[m3u] трек добавлен в {os.path.basename(m3u_path)}")


def place_file(input_path: str, song, template: str, fmt: str, bitrate: str,
               log=print, m3u_path: str = None) -> str:
    """Конвертирует input_path в формат библиотеки, кладёт по шаблону и вшивает
    метаданные/обложку из song. Если задан m3u_path — дописывает трек в плейлист
    (без полного sync). Возвращает итоговый путь."""
    from spotdl.utils.formatter import create_file_name
    from spotdl.utils.ffmpeg import convert
    from spotdl.utils.metadata import embed_metadata

    out = create_file_name(song, template, fmt)
    # spotdl 4.5.0 прогоняет части пути через sanitize (вырезает '/'), из-за чего
    # абсолютный путь может стать относительным — возвращаем под корень.
    if not out.is_absolute():
        out = Path("/") / out
    out.parent.mkdir(parents=True, exist_ok=True)

    log(f"[ffmpeg] конвертация -> {out}")
    bps = None if str(bitrate).lower() in ("", "auto", "none", "disable") else bitrate
    success, _ = convert(
        input_file=Path(input_path),
        output_file=out,
        ffmpeg="ffmpeg",
        output_format=fmt,
        bitrate=bps,
    )
    if not success:
        raise RuntimeError("ffmpeg: конвертация не удалась")

    log("[meta] вшиваю теги и обложку")
    embed_metadata(out, song)

    if m3u_path:
        try:
            append_to_m3u(str(out), song, m3u_path, template, fmt, log=log)
        except Exception as exc:  # noqa: BLE001 — m3u не критичен для самого файла
            log(f"[m3u] не удалось обновить плейлист: {exc}")
    return str(out)
