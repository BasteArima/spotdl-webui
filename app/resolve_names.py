"""Резолв реальных названий треков со Spotify по URL — для строк, где в errors-
файле имя битое (напр. 'musicShelfRenderer': spotdl при сбое парсинга YTM пишет
внутренний ключ структуры вместо «Артист - Название»).

    python -m app.resolve_names <spotify_url> [<spotify_url> ...]

Печатает JSON {url: "Artist - Title"}. Запускается в подпроцессе, чтобы тяжёлый
импорт spotdl не висел в веб-процессе."""
import json
import sys


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print("{}")
        return
    from app import library
    library.init_spotify()
    out = {}
    for url in urls:
        try:
            song = library.fetch_song(url)
            name = (song.display_name or "").strip()
            if name:
                out[url] = name
        except Exception:  # noqa: BLE001 — недоступный трек просто пропускаем
            pass
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
