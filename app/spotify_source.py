"""Список треков источника Spotify — без загрузки (design/plan.md, задача 3.1).

    python -m app.spotify_source <ссылка> <файл>

Пишет в <файл> треки списком в формате spotdl (как save-файл `download`), в
stdout последней строкой — ASCII-JSON {"ok", "count", "name"} или
{"ok": false, "error", "unavailable"}.

Берёт то же, что spotdl в начале `sync`, и столько же запросов: плейлист — 1 +
по одному на каждые 100 треков, альбом — 1 + на каждые 50, исполнитель — все его
альбомы. Метаданные треков (жанры и т.п.) spotdl дозапрашивает потом только
для того, что действительно скачивает. Liked Songs — отдельно
(`app.spotify_login --saved-songs`, от имени пользователя).

Отдельным процессом: spotdl и spotipy тяжёлые, веб их не держит.
"""
import json
import os
import sys


def fetch(url: str) -> dict:
    from spotdl.types.album import Album
    from spotdl.types.artist import Artist
    from spotdl.types.playlist import Playlist
    from spotdl.types.song import Song

    from app import library
    from app.naming import url_type

    library.init_spotify()
    kind = url_type(url)
    if kind == "track":
        song = Song.from_url(url)
        return {"name": song.display_name, "songs": [song.json]}
    cls = {"playlist": Playlist, "album": Album, "artist": Artist}.get(kind)
    if cls is None:
        raise ValueError(f"не ссылка Spotify на плейлист, альбом, исполнителя или трек: {url}")
    obj = cls.from_url(url, fetch_songs=False)
    songs = []
    for pos, song in enumerate(obj.songs, 1):
        d = song.json
        d["list_name"], d["list_url"] = obj.name, obj.url
        d["list_position"] = d.get("list_position") or pos
        d["list_length"] = len(obj.songs)
        songs.append(d)
    return {"name": obj.name, "songs": songs}


def _unavailable(exc: Exception) -> bool:
    """Площадка не отдаёт список: персональные подборки (Daily Mix, Discover
    Weekly), закрытые и удалённые плейлисты. spotdl 4.5 по умолчанию ходит через
    SpotipyFree (веб-плеер, без приложения разработчика) — у него своё сообщение."""
    status = getattr(exc, "http_status", None)
    text = str(exc).lower()
    return (status in (403, 404) or "not available or not accessible" in text or "not found" in text
            or "invalid playlist" in text or "wrong playlist" in text)


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: spotify_source <url> <out_file>", file=sys.stderr)
        sys.exit(2)
    url, out_path = sys.argv[1], sys.argv[2]
    try:
        res = fetch(url)
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(res["songs"], fh, ensure_ascii=False)
        os.replace(tmp, out_path)
        out = {"ok": True, "count": len(res["songs"]), "name": res["name"]}
    except Exception as exc:  # noqa: BLE001 — веб должен получить JSON, а не трейсбек
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500], "unavailable": _unavailable(exc)}
    print(json.dumps(out), flush=True)
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
