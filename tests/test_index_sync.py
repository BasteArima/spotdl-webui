"""Тесты синка через индекс (app/index_sync.py), m3u из индекса (app/m3u_gen.py)
и плейлистов в индексе (app/playlists.py). Без сети: список и итоги spotdl —
подставные. Запуск: python tests/test_index_sync.py (нужны ffmpeg и spotdl)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = tempfile.mkdtemp(prefix="isync-")
os.environ["MUSIC_DIR"] = os.path.join(_ROOT, "music")
os.environ["CONF_DIR"] = os.path.join(_ROOT, "conf")
os.environ["PLAYLISTS_M3U_DIR"] = os.path.join(_ROOT, "music", "playlists")
os.environ["ERRORS_DIR"] = os.path.join(_ROOT, "conf", "errors")
os.environ["PLAYLISTS_FILE"] = os.path.join(_ROOT, "conf", "playlists.txt")

from app import config, db, index_sync, legacy_import, m3u_gen, playlists, scanner  # noqa: E402
from app.naming import playlist_id  # noqa: E402

PL_URL = "https://open.spotify.com/playlist/37i9dQZF1DX0000000000001"
SP = {n: f"{n:0>22}" for n in "abcdefgh"}


def _song(key, name, artist="Artist", duration=200, **kw):
    # как отдаёт список SpotipyFree: без альбома и ISRC
    d = {"name": name, "artists": [artist], "artist": artist, "song_id": SP[key],
         "url": f"https://open.spotify.com/track/{SP[key]}", "isrc": "", "duration": duration,
         "track_number": 1, "disc_number": 1, "album_name": None, "album_artist": None, "genres": None}
    d.update(kw)
    return d


def _file(rel, name, key=None, artist="Artist", seconds=200):
    """mp3 нужной длины с тегами spotdl (ссылка Spotify — если key задан;
    ISRC spotdl пишет всегда — свой у каждого файла)."""
    from pathlib import Path
    from spotdl.types.song import Song
    from spotdl.utils.metadata import embed_metadata
    path = os.path.join(config.MUSIC_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                    "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "8k", path], check=True)
    url = f"https://open.spotify.com/track/{SP[key]}" if key else ""
    s = Song.from_missing_data(name=name, artists=[artist], artist=artist, album_name="Al",
                               album_artist=artist, genres=[], disc_number=1, disc_count=1, track_number=1,
                               tracks_count=1, year=2024, date="2024", song_id=SP[key] if key else "",
                               url=url, isrc="QZ" + format(abs(hash(rel)) % 10**10, "010d"), duration=seconds, explicit=False, publisher="",
                               cover_url=None, copyright_text=None, lyrics=None)
    embed_metadata(Path(path), s, skip_album_art=True)
    return os.path.abspath(path)


def _setup():
    shutil.rmtree(_ROOT, ignore_errors=True)
    os.makedirs(config.ERRORS_DIR)
    with open(config.PLAYLISTS_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"Для работы|{PL_URL}\nЛюбимые|saved\n")
    files = {"a": _file("spotify/Artist/Al/a.mp3", "Alpha", "a"),
             # тот же трек, но другой релиз: ссылки Spotify в тегах нет
             "c": _file("spotify/Artist/Other/c.mp3", "Charlie", None)}
    scanner.scan()
    playlists.read_playlists()          # перенос playlists.txt в индекс
    return files


def _ids(conn):
    pid = playlists.db_id(conn, PL_URL)
    sid = conn.execute("SELECT id FROM playlist_sources WHERE playlist_id = ?", (pid,)).fetchone()[0]
    return pid, sid


def test_playlists_move_to_index_and_export():
    _setup()
    pls = playlists.read_playlists()
    assert [(p.name, p.url) for p in pls] == [("Для работы", PL_URL), ("Любимые", "saved")]
    assert pls[0].id == playlist_id(PL_URL)
    playlists.add_playlist("Альбом", "https://open.spotify.com/album/0jV32pIk8l3vTaU3thTaW2")
    playlists.update_playlist("saved", "Лайки", "saved")
    with open(config.PLAYLISTS_FILE, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if not ln.startswith("#")]
    assert lines == [f"Для работы|{PL_URL}", "Лайки|saved",
                     "Альбом|https://open.spotify.com/album/0jV32pIk8l3vTaU3thTaW2"], lines
    for bad in ("https://example.com/x", ""):
        try:
            playlists.add_playlist("Плохая", bad)
            raise AssertionError(f"приняли {bad!r}")
        except ValueError:
            pass


def test_reconcile_finds_present_and_other_release():
    files = _setup()
    songs = [_song("a", "Alpha"), _song("b", "Bravo"), _song("c", "Charlie"), _song("a", "Alpha")]
    with db.connect() as conn, db.transaction(conn):
        pid, sid = _ids(conn)
        plan = index_sync.reconcile(conn, pid, sid, songs, time.time())
    assert plan["tracks"] == 3 and plan["present"] == 2 and plan["linked"] == 1, plan
    assert [s["name"] for _t, s in plan["to_fetch"]] == ["Bravo"]
    with db.connect() as conn:
        # Charlie связан с файлом другого релиза — и запомнил свой ID Spotify
        row = conn.execute("SELECT t.path FROM track_ids i JOIN tracks t ON t.id = i.track_id "
                           "WHERE i.kind = 'spotify' AND i.value = ?", (SP["c"],)).fetchone()
        assert row and row[0] == files["c"], row
        n = conn.execute("SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?", (pid,)).fetchone()[0]
        assert n == 3


def test_apply_results_and_m3u():
    files = _setup()
    songs = [_song("a", "Alpha"), _song("b", "Bravo"), _song("d", "Delta"), _song("e", "Echo"),
             _song("f", "Foxtrot")]
    with db.connect() as conn, db.transaction(conn):
        pid, sid = _ids(conn)
        index_sync.reconcile(conn, pid, sid, songs, time.time())
        conn.execute("UPDATE playlists SET managed = 1 WHERE id = ?", (pid,))
    bravo = _file("spotify/Artist/Al/b.mp3", "Bravo", "b")                 # spotdl скачал
    results = [
        {"url": f"https://open.spotify.com/track/{SP['b']}", "path": bravo, "existed": False, "error": None},
        {"url": f"https://open.spotify.com/track/{SP['d']}", "path": None,
         "error": "LookupError: No results found for song: Artist - Delta"},
        {"url": f"https://open.spotify.com/track/{SP['e']}", "path": None,
         "error": "AudioProviderError: YT-DLP download error - https://music.youtube.com/watch?v=abcdefghijk"},
        # spotdl нашёл файл под другим именем — он уже в индексе
        {"url": f"https://open.spotify.com/track/{SP['f']}", "path": files["c"], "existed": True, "error": None},
    ]
    with db.connect() as conn, db.transaction(conn):
        st = index_sync.apply_results(conn, results, time.time())
    assert st["downloaded"] == 1 and st["linked"] == 1 and st["failed"] == 2, st
    assert st["by_reason"] == {"not_found": 1, "download_failed": 1}, st
    with db.connect() as conn:
        m = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT t.title, m.reason, m.source_url FROM missing m JOIN tracks t ON t.id = m.track_id")}
        assert m == {"Delta": ("not_found", None),
                     "Echo": ("download_failed", "https://music.youtube.com/watch?v=abcdefghijk")}, m
        assert m3u_gen.write_playlist(conn, pid)
        assert not m3u_gen.write_playlist(conn, pid)            # не изменилось — не переписывается
    with open(m3u_gen.path_for("Для работы"), encoding="utf-8") as fh:
        text = fh.read()
    paths = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert paths == [files["a"], bravo, files["c"]], paths       # только скачанное, по порядку, без повторов
    assert text.startswith("#EXTM3U\n#EXTINF:200,Artist - Alpha\n"), text[:80]
    # сканер дочитывает теги нового файла — трек тот же
    scanner.scan()
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM tracks WHERE title = 'Bravo'").fetchone()[0]
        assert n == 1


def test_managed_playlist_untouched_by_legacy_import():
    _setup()
    with db.connect() as conn, db.transaction(conn):
        pid, sid = _ids(conn)
        index_sync.reconcile(conn, pid, sid, [_song("a", "Alpha")], time.time())
        conn.execute("UPDATE playlists SET managed = 1 WHERE id = ?", (pid,))
    # старый save-файл с другим составом — импорт его не применяет
    with open(config.savefile_path(playlist_id(PL_URL)), "w", encoding="utf-8") as fh:
        json.dump({"type": "sync", "songs": [_song("g", "Golf"), _song("h", "Hotel")]}, fh)
    rep = legacy_import.import_legacy(force=True)
    assert rep["playlists_managed"] == 1, rep
    with db.connect() as conn:
        titles = [r[0] for r in conn.execute("SELECT t.title FROM playlist_items pi JOIN tracks t "
                                             "ON t.id = pi.track_id WHERE pi.playlist_id = ?", (pid,))]
    assert titles == ["Alpha"], titles


def test_auto_fetch_marks_attempt():
    _setup()
    with db.connect() as conn, db.transaction(conn):
        pid, sid = _ids(conn)
        index_sync.reconcile(conn, pid, sid, [_song("d", "Delta"), _song("e", "Echo")], time.time())
        index_sync.apply_results(conn, [
            {"url": f"https://open.spotify.com/track/{SP['d']}", "error": "LookupError: No results"},
            {"url": f"https://open.spotify.com/track/{SP['e']}", "error": "AudioProviderError: blocked"}],
            time.time())
        first = index_sync.auto_fetch_candidates(conn, pid, "deezer", time.time())
        again = index_sync.auto_fetch_candidates(conn, pid, "deezer", time.time())
        later = index_sync.auto_fetch_candidates(conn, pid, "deezer", time.time() + 8 * 86400)
        other = index_sync.auto_fetch_candidates(conn, pid, "auto", time.time())
    # только «нет на YouTube» (не блок YouTube), не чаще раза в неделю на источник
    assert [x["subject"] for x in first] == ["Artist - Delta"], first
    assert again == [] and len(later) == 1 and len(other) == 1


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("SKIP: нет ffmpeg")
        sys.exit(0)
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    shutil.rmtree(_ROOT, ignore_errors=True)
    sys.exit(1 if failures else 0)
