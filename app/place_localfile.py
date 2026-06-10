"""Размещение ЛОКАЛЬНОГО аудиофайла в библиотеке с метаданными со Spotify.

Запускается как отдельный процесс (как и spotdl), чтобы тяжёлые импорты spotdl
не висели в веб-процессе:

    python -m app.place_localfile <input_file> <spotify_track_url> \
        <output_template> <format> <bitrate>

Делает ровно то, что spotdl делает ПОСЛЕ скачивания аудио с YouTube:
метаданные со Spotify → путь по тому же шаблону → ffmpeg-конвертация в формат
библиотеки → вшивание тегов и обложки. Печатает `OK <path>` при успехе.
"""
import sys
from pathlib import Path

# Публичные client_id/secret, которые spotdl использует по умолчанию
# (spotdl/utils/config.py). Тот же доступ, что и при обычном `spotdl download`.
_DEFAULT_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
_DEFAULT_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"


def main() -> None:
    if len(sys.argv) < 6:
        print("usage: place_localfile <input> <spotify_url> <template> <format> <bitrate>",
              file=sys.stderr)
        sys.exit(2)
    input_file, spotify_url, template, fmt, bitrate = sys.argv[1:6]

    from spotdl.utils.spotify import SpotifyClient
    from spotdl.types.song import Song
    from spotdl.utils.formatter import create_file_name
    from spotdl.utils.ffmpeg import convert
    from spotdl.utils.metadata import embed_metadata

    SpotifyClient.init(
        client_id=_DEFAULT_CLIENT_ID,
        client_secret=_DEFAULT_CLIENT_SECRET,
        no_cache=True,
    )

    print(f"[meta] запрашиваю метаданные: {spotify_url}", flush=True)
    song = Song.from_url(spotify_url)
    print(f"[meta] трек: {song.display_name}", flush=True)

    out = create_file_name(song, template, fmt)
    # spotdl 4.5.0 при сборке имени прогоняет части через sanitize (вырезает '/'),
    # из-за чего абсолютный путь может стать относительным — возвращаем под корень.
    if not out.is_absolute():
        out = Path("/") / out
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"[ffmpeg] конвертация -> {out}", flush=True)
    bitrate_arg = None if str(bitrate).lower() in ("", "auto", "none", "disable") else bitrate
    success, _ = convert(
        input_file=Path(input_file),
        output_file=out,
        ffmpeg="ffmpeg",
        output_format=fmt,
        bitrate=bitrate_arg,
    )
    if not success:
        print("[error] ffmpeg: конвертация не удалась", file=sys.stderr)
        sys.exit(3)

    print("[meta] вшиваю теги и обложку", flush=True)
    embed_metadata(out, song)
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
