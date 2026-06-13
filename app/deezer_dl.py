"""Deezer-фолбэк: скачать трек с Deezer по ISRC (точное совпадение со Spotify),
дать ему метаданные/обложку со Spotify и положить в библиотеку по шаблону.

    python -m app.deezer_dl <spotify_track_url> <output_template> <format> <bitrate>

ARL берётся из окружения DEEZER_ARL или файла /conf/deezer_arl.txt (config).
Печатает `OK <path>` при успехе.
"""
import os
import sys


def main() -> None:
    if len(sys.argv) < 5:
        print("usage: deezer_dl <spotify_url> <template> <format> <bitrate>", file=sys.stderr)
        sys.exit(2)
    spotify_url, template, fmt, bitrate = sys.argv[1:5]

    from app import config, deezer, library

    arl = config.deezer_arl()
    if not arl:
        print("[error] DEEZER_ARL не настроен (env DEEZER_ARL или /conf/deezer_arl.txt)",
              file=sys.stderr)
        sys.exit(4)

    library.init_spotify()
    print(f"[meta] запрашиваю метаданные: {spotify_url}", flush=True)
    song = library.fetch_song(spotify_url)
    print(f"[meta] трек: {song.display_name}  ISRC={song.isrc}", flush=True)
    if not song.isrc:
        print("[error] у трека нет ISRC — сопоставление с Deezer невозможно", file=sys.stderr)
        sys.exit(5)

    print(f"[deezer] ищу по ISRC {song.isrc}", flush=True)
    tmp, dz_fmt = deezer.download_by_isrc(song.isrc, arl, config.UPLOADS_DIR)
    print(f"[deezer] скачано ({dz_fmt}): {tmp}", flush=True)

    try:
        out = library.place_file(tmp, song, template, fmt, bitrate,
                                 log=lambda m: print(m, flush=True))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
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
