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


def place_file(input_path: str, song, template: str, fmt: str, bitrate: str, log=print) -> str:
    """Конвертирует input_path в формат библиотеки, кладёт по шаблону и вшивает
    метаданные/обложку из song. Возвращает итоговый путь."""
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
    return str(out)
