"""Размещение ЛОКАЛЬНОГО аудиофайла в библиотеке с метаданными со Spotify.

    python -m app.place_localfile <input_file> <spotify_track_url> \
        <output_template> <format> <bitrate>

Печатает `OK <path>` при успехе. Тяжёлая логика — в app.library.
"""
import sys


def main() -> None:
    if len(sys.argv) < 6:
        print("usage: place_localfile <input> <spotify_url> <template> <format> <bitrate>",
              file=sys.stderr)
        sys.exit(2)
    input_file, spotify_url, template, fmt, bitrate = sys.argv[1:6]

    from app import library

    library.init_spotify()
    print(f"[meta] запрашиваю метаданные: {spotify_url}", flush=True)
    song = library.fetch_song(spotify_url)
    print(f"[meta] трек: {song.display_name}", flush=True)

    out = library.place_file(input_file, song, template, fmt, bitrate,
                             log=lambda m: print(m, flush=True))
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
