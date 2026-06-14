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
        print("usage: deezer_dl <spotify_url> <template> <format> <bitrate> [safe]", file=sys.stderr)
        sys.exit(2)
    spotify_url, template, fmt, bitrate = sys.argv[1:5]
    safe = sys.argv[5] if len(sys.argv) > 5 else ""

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

    sng_id = None
    if song.isrc:
        print(f"[deezer] ищу по ISRC {song.isrc}", flush=True)
        sng_id = deezer.resolve_isrc(song.isrc)
    if sng_id is None:
        print(f"[deezer] нет ISRC/не нашёлся — ищу по названию: {song.artist} - {song.name}", flush=True)
        sng_id = deezer.search_track(song.artist, song.name)
    if sng_id is None:
        print("[error] трек не найден в каталоге Deezer", file=sys.stderr)
        sys.exit(5)

    tmp, dz_fmt = deezer.download_track(sng_id, arl, config.UPLOADS_DIR)
    print(f"[deezer] скачано ({dz_fmt}): {tmp}", flush=True)

    m3u = config.m3u_path(safe) if safe else None
    try:
        out = library.place_file(tmp, song, template, fmt, bitrate,
                                 log=lambda m: print(m, flush=True), m3u_path=m3u)
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
