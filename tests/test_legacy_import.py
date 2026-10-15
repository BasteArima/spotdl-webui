"""Тесты переноса playlists.txt / save-файлов / errors в индекс
(app/legacy_import.py) и слияния «не скачан» → файл в сканере.
Запуск: python tests/test_legacy_import.py (нужны ffmpeg и spotdl)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = tempfile.mkdtemp(prefix="legacy-")
os.environ["MUSIC_DIR"] = os.path.join(_ROOT, "music")
os.environ["CONF_DIR"] = os.path.join(_ROOT, "conf")
os.environ["PLAYLISTS_M3U_DIR"] = os.path.join(_ROOT, "music", "playlists")
os.environ["ERRORS_DIR"] = os.path.join(_ROOT, "conf", "errors")
os.environ["PLAYLISTS_FILE"] = os.path.join(_ROOT, "conf", "playlists.txt")

from app import config, db, legacy_import, playlists, scanner  # noqa: E402
from app.naming import playlist_id, safe_name  # noqa: E402

PL_URL = "https://open.spotify.com/playlist/37i9dQZF1DX0000000000001"
AL_URL = "https://open.spotify.com/album/0jV32pIk8l3vTaU3thTaW2"
SP = {n: f"{n:0>22}" for n in ("a", "b", "c", "d", "e", "f", "g", "h")}   # ID Spotify (22 символа)


def _song(key, name, artist="Artist", isrc=None, **kw):
    d = {"name": name, "artists": [artist], "artist": artist, "album_name": "Al", "album_artist": artist,
         "song_id": SP[key], "url": f"https://open.spotify.com/track/{SP[key]}", "isrc": isrc,
         "duration": 200, "track_number": 1, "disc_number": 1, "year": 2024}
    d.update(kw)
    return d


def _file(rel, key, name, isrc="QZABC2400001"):
    from pathlib import Path
    from spotdl.types.song import Song
    from spotdl.utils.metadata import embed_metadata
    path = os.path.join(config.MUSIC_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "1", "-c:a", "libmp3lame", path], check=True)
    s = Song.from_missing_data(name=name, artists=["Artist"], artist="Artist", album_name="Al",
                               album_artist="Artist", genres=[], disc_number=1, disc_count=1, track_number=1,
                               tracks_count=1, year=2024, date="2024", song_id=SP[key],
                               url=f"https://open.spotify.com/track/{SP[key]}", isrc=isrc, duration=1,
                               explicit=False, publisher="", cover_url=None, copyright_text=None, lyrics=None)
    embed_metadata(Path(path), s, skip_album_art=True)
    return os.path.abspath(path)


def _setup():
    shutil.rmtree(_ROOT, ignore_errors=True)
    os.makedirs(config.ERRORS_DIR)
    with open(config.PLAYLISTS_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"Для работы|{PL_URL}\nЛюбимые|saved\nArcane: OST|{AL_URL}\n"
                 "Странная|https://example.com/whatever\n")
    _file("spotify/Artist/Al/a.mp3", "a", "Alpha")
    _file("spotify/Artist/Al/b.mp3", "b", "Bravo", isrc="USAAA2400002")
    _file("spotify/Artist/Al/x.mp3", "h", "Other release of Echo", isrc="USEEE2400005")
    # плейлист (формат sync): a — есть файл, c — нет на YouTube, d — YouTube не отдал,
    # e — нет файла по ID, но тот же ISRC есть у файла другого релиза, a ещё раз (дубль строки)
    with open(config.savefile_path(playlist_id(PL_URL)), "w", encoding="utf-8") as fh:
        json.dump({"type": "sync", "query": [PL_URL], "songs": [
            _song("a", "Alpha"), _song("c", "Charlie"), _song("d", "Delta"),
            _song("e", "Echo", isrc="USEEE2400005"), _song("a", "Alpha")]}, fh)
    with open(config.errors_path(safe_name("Для работы")), "w", encoding="utf-8") as fh:
        fh.write("2026-10-15-04-00-00\n"
                 f"https://open.spotify.com/track/{SP['c']} - LookupError: No results found for song: Artist - Charlie\n"
                 f"https://open.spotify.com/track/{SP['d']} - AudioProviderError: YT-DLP download error - "
                 "https://music.youtube.com/watch?v=abcdefghijk\n")
    # Любимые (формат download — список): a — общий с «Для работы», b — есть, f — без объяснений
    with open(config.savefile_path(playlist_id("saved")), "w", encoding="utf-8") as fh:
        json.dump([_song("a", "Alpha"), _song("b", "Bravo"), _song("f", "Foxtrot")], fh)
    # у альбома save-файла нет — только errors-файл
    with open(config.errors_path(safe_name("Arcane: OST")), "w", encoding="utf-8") as fh:
        fh.write(f"2026-10-15-04-00-00\nhttps://open.spotify.com/track/{SP['g']} - LookupError: "
                 "No results found for song: Artist - Golf\n")


def _q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def test_import_playlists_items_and_reasons():
    _setup()
    scanner.scan()
    rep = legacy_import.import_legacy()
    # список переносится в индекс до импорта (playlists.ensure_db_master); ссылка не Spotify — не переносится
    assert rep["playlists"] == 3 and rep["playlists_new"] == 0 and not rep["skipped"], rep
    with db.connect() as conn:
        kinds = sorted(r[0] for r in _q(conn, "SELECT kind FROM playlist_sources"))
        assert kinds == ["spotify_album", "spotify_liked", "spotify_playlist"], kinds
        assert _q(conn, "SELECT ref FROM playlist_sources WHERE kind = 'spotify_liked'")[0][0] == "saved"

        work = _q(conn, "SELECT id FROM playlists WHERE name = 'Для работы'")[0][0]
        items = _q(conn, "SELECT t.title, t.status FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id "
                         "WHERE pi.playlist_id = ? ORDER BY pi.position", work)
        assert [tuple(r) for r in items] == [("Alpha", "ok"), ("Charlie", "missing"), ("Delta", "missing"),
                                              ("Other release of Echo", "ok")], [tuple(r) for r in items]

        reason = {r[0]: (r[1], r[2]) for r in _q(conn, "SELECT t.title, m.reason, m.source_url FROM missing m "
                                                       "JOIN tracks t ON t.id = m.track_id")}
        assert reason["Charlie"][0] == "not_found", reason
        assert reason["Delta"] == ("download_failed", "https://music.youtube.com/watch?v=abcdefghijk"), reason
        assert reason["Foxtrot"][0] == "other", reason       # в Любимых, но ни файла, ни ошибки
        assert reason["Golf"][0] == "not_found", reason       # альбом: только errors-файл

        # Alpha в двух плейлистах — один трек, один файл
        alpha = _q(conn, "SELECT COUNT(*) FROM tracks WHERE title = 'Alpha'")[0][0]
        assert alpha == 1
        assert rep["unexplained"] == 1, rep
        t = rep["totals"]
        assert t["in_playlists"] == 7 and t["downloaded"] == 3 and t["missing"] == 4, t
        assert t["missing_by_reason"] == {"not_found": 2, "download_failed": 1, "other": 1}, t


def test_reimport_is_idempotent_and_skips_unchanged():
    _setup()
    scanner.scan()
    legacy_import.import_legacy()
    with db.connect() as conn:
        before = _q(conn, "SELECT COUNT(*) FROM tracks")[0][0], _q(conn, "SELECT COUNT(*) FROM playlist_items")[0][0]
    rep = legacy_import.import_legacy()
    assert rep["playlists_unchanged"] == 3 and rep["playlists_new"] == 0, rep
    rep = legacy_import.import_legacy(force=True)
    assert rep["playlists_unchanged"] == 0, rep
    with db.connect() as conn:
        after = _q(conn, "SELECT COUNT(*) FROM tracks")[0][0], _q(conn, "SELECT COUNT(*) FROM playlist_items")[0][0]
    assert before == after, (before, after)


def test_downloaded_later_merges_into_playlists():
    _setup()
    scanner.scan()
    legacy_import.import_legacy()
    with db.connect() as conn:
        missing_id = _q(conn, "SELECT id FROM tracks WHERE title = 'Charlie'")[0][0]
    path = _file("spotify/Artist/Al/c.mp3", "c", "Charlie")       # трек наконец скачался
    res = scanner.scan()
    assert res["new"] == 1 and res["id_conflicts"] == 0, res
    with db.connect() as conn:
        assert _q(conn, "SELECT COUNT(*) FROM tracks WHERE id = ?", missing_id)[0][0] == 0
        row = _q(conn, "SELECT id, status FROM tracks WHERE path = ?", path)[0]
        assert row["status"] == "ok"
        assert _q(conn, "SELECT COUNT(*) FROM playlist_items WHERE track_id = ?", row["id"])[0][0] == 1
        assert _q(conn, "SELECT COUNT(*) FROM missing WHERE track_id = ?", missing_id)[0][0] == 0
        assert _q(conn, "SELECT COUNT(*) FROM match_candidates")[0][0] == 0


def test_removed_playlist_and_orphans():
    _setup()
    scanner.scan()
    legacy_import.import_legacy()
    # правка файла руками больше ничего не удаляет: главный список — в индексе
    with open(config.PLAYLISTS_FILE, "w", encoding="utf-8") as fh:
        fh.write("Любимые|saved\n")
    legacy_import.import_legacy()
    assert len(playlists.read_playlists()) == 3
    assert playlists.delete_playlist(PL_URL) and playlists.delete_playlist(AL_URL)
    with open(config.PLAYLISTS_FILE, encoding="utf-8") as fh:
        assert [ln for ln in fh.read().splitlines() if not ln.startswith("#")] == ["Любимые|saved"]
    with db.connect() as conn:
        titles = {r[0] for r in _q(conn, "SELECT title FROM tracks WHERE status = 'missing'")}
        assert titles == {"Foxtrot"}, titles             # Charlie/Delta/Golf больше никому не нужны
        assert _q(conn, "SELECT COUNT(*) FROM tracks WHERE status = 'ok'")[0][0] == 3   # файлы не трогаются


def test_cli_runs_scan_and_import():
    _setup()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "app.scanner"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert last["ok"] and last["import"]["totals"]["missing"] == 4, last
    assert "[import] плейлистов 3" in r.stdout, r.stdout


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
