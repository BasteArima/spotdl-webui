"""Лёгкая дозапись скачанного трека в m3u плейлиста — БЕЗ полного spotdl sync.

    python -m app.append_m3u <spotify_track_url> <safe>

Используется после `spotdl download` (YouTube-источник): сам аудиофайл spotdl уже
положил по шаблону, но m3u-плейлист он не трогает. Раньше для обновления m3u
запускался ТЯЖЁЛЫЙ `spotdl sync` ВСЕГО плейлиста (сотни треков, минуты, блокировал
очередь и заодно пытался до-скачать всё подряд). Вместо этого дописываем ровно один
трек — так же, как Deezer/заливка файла (library.append_to_m3u).

Печатает `OK <m3u>` при успехе.
"""
import os
import sys


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: append_m3u <spotify_url> <safe>", file=sys.stderr)
        sys.exit(2)
    spotify_url, safe = sys.argv[1], sys.argv[2]
    if not safe:
        print("[m3u] safe пуст — пропуск дозаписи", flush=True)
        print("OK skipped", flush=True)
        return

    from app import config, library

    library.init_spotify()
    song = library.fetch_song(spotify_url)
    out = library.target_path(song, config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT)
    if not os.path.exists(out):
        # путь не совпал с тем, что положил spotdl — не критично: m3u поправит автосинк
        print(f"[m3u] файл не найден ({out}) — пропуск дозаписи", flush=True)
        print("OK skipped", flush=True)
        return
    m3u = config.m3u_path(safe)
    library.append_to_m3u(str(out), song, m3u, config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT,
                          log=lambda m: print(m, flush=True))
    print(f"OK {m3u}", flush=True)


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
