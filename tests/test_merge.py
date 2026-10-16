"""Тесты склейки дублей и корзины (app/merge.py).
Запуск: python tests/test_merge.py (нужны ffmpeg и spotdl)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = tempfile.mkdtemp(prefix="merge-")
os.environ["MUSIC_DIR"] = os.path.join(_ROOT, "music")
os.environ["CONF_DIR"] = os.path.join(_ROOT, "conf")
os.environ["PLAYLISTS_M3U_DIR"] = os.path.join(_ROOT, "music", "playlists")
os.environ["ERRORS_DIR"] = os.path.join(_ROOT, "conf", "errors")
os.environ["PLAYLISTS_FILE"] = os.path.join(_ROOT, "conf", "playlists.txt")

from app import config, db, dedupe, index_sync, m3u_gen, merge, playlists, scanner  # noqa: E402

PL_URL = "https://open.spotify.com/playlist/37i9dQZF1DX0000000000001"
SP = {n: f"{n:0>22}" for n in "abcdefgh"}


def _file(rel, name, key, seconds=200, kbps=8, isrc=None, artist="Artist"):
    from pathlib import Path
    from spotdl.types.song import Song
    from spotdl.utils.metadata import embed_metadata
    path = os.path.join(config.MUSIC_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                    "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", f"{kbps}k", path], check=True)
    s = Song.from_missing_data(name=name, artists=[artist], artist=artist, album_name="Al", album_artist=artist,
                               genres=[], disc_number=1, disc_count=1, track_number=1, tracks_count=1, year=2024,
                               date="2024", song_id=SP[key], url=f"https://open.spotify.com/track/{SP[key]}",
                               isrc=isrc or "QZ" + format(abs(hash(rel)) % 10**10, "010d"), duration=seconds,
                               explicit=False, publisher="", cover_url=None, copyright_text=None, lyrics=None)
    embed_metadata(Path(path), s, skip_album_art=True)
    return os.path.abspath(path)


def _setup():
    """Два файла одного трека Spotify (два релиза), лучший — 32k; плейлист через
    индекс содержит худший; прежний m3u (как пишет spotdl) — тоже худший."""
    shutil.rmtree(_ROOT, ignore_errors=True)
    os.makedirs(config.ERRORS_DIR)
    with open(config.PLAYLISTS_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"Для работы|{PL_URL}\n")
    worse = _file("spotify/Artist/TIME/01 - Song.mp3", "Song", "a", kbps=8)
    with open(worse[:-4] + ".lrc", "w") as fh:
        fh.write("[00:01.00]la")
    better = _file("spotify/Artist/Time (Deluxe)/01 - Song.mp3", "Song", "a", kbps=32)
    # spotdl взял один ролик для двух разных песен — общий ID, разные названия
    _file("spotify/Artist/Al/b1.mp3", "The Beast", "b")
    _file("spotify/Artist/Al/b2.mp3", "Playground", "b")
    scanner.scan()
    playlists.read_playlists()
    with db.connect() as conn, db.transaction(conn):
        pid = playlists.db_id(conn, PL_URL)
        sid = conn.execute("SELECT id FROM playlist_sources WHERE playlist_id = ?", (pid,)).fetchone()[0]
        wid = conn.execute("SELECT id FROM tracks WHERE path = ?", (worse,)).fetchone()[0]
        conn.execute("INSERT INTO playlist_items(playlist_id, source_id, track_id, position, added_at) "
                     "VALUES (?, ?, ?, 0, ?)", (pid, sid, wid, time.time()))
        conn.execute("UPDATE playlists SET managed = 1 WHERE id = ?", (pid,))
    dedupe.find_duplicates()
    os.makedirs(config.PLAYLISTS_M3U_DIR, exist_ok=True)
    with open(os.path.join(config.PLAYLISTS_M3U_DIR, "Старый.m3u8"), "w", encoding="utf-8") as fh:
        fh.write(f"#EXTM3U\n#EXTINF:200,Artist - Song\n{worse}\n#EXTINF:200,Artist - Song\n{better}\n")
    return worse, better, pid


def _pairs(conn):
    return conn.execute("SELECT c.id, c.level, a.title AS ta, b.title AS tb FROM match_candidates c "
                        "JOIN tracks a ON a.id = c.track_a JOIN tracks b ON b.id = c.track_b").fetchall()


def test_auto_eligible_skips_shared_id_with_other_title():
    _setup()
    with db.connect() as conn:
        pairs = {(r["ta"], r["tb"]): r["id"] for r in _pairs(conn)}
        safe = set(merge.auto_eligible(conn))
    song = pairs[("Song", "Song")]
    beast = next(v for k, v in pairs.items() if "The Beast" in k)
    assert song in safe and beast not in safe, (pairs, safe)


def test_merge_moves_worse_file_to_trash_and_fixes_playlists():
    worse, better, pid = _setup()
    rep = merge.apply(auto=True, log=lambda m: None)
    assert rep["merged"] == 1 and rep["auto"] == 1, rep
    trash = os.path.join(config.MUSIC_DIR, ".trash", "spotify", "Artist", "TIME", "01 - Song.mp3")
    assert not os.path.exists(worse) and os.path.exists(trash) and os.path.exists(trash[:-4] + ".lrc")
    assert os.path.exists(os.path.join(config.MUSIC_DIR, ".trash", ".ndignore"))
    with db.connect() as conn:
        bid = conn.execute("SELECT id FROM tracks WHERE path = ?", (better,)).fetchone()[0]
        items = [r[0] for r in conn.execute("SELECT track_id FROM playlist_items WHERE playlist_id = ?", (pid,))]
        assert items == [bid], items                                # плейлист — на лучший файл
        t = conn.execute("SELECT status, trashed_from, merged_into FROM tracks WHERE path = ?", (trash,)).fetchone()
        assert tuple(t) == ("trashed", worse, bid), tuple(t)
        owner = conn.execute("SELECT track_id FROM track_ids WHERE kind = 'spotify' AND value = ?", (SP["a"],)).fetchone()[0]
        assert owner == bid
    with open(m3u_gen.path_for("Для работы"), encoding="utf-8") as fh:
        assert [ln for ln in fh.read().splitlines() if not ln.startswith("#")] == [better]
    with open(os.path.join(config.PLAYLISTS_M3U_DIR, "Старый.m3u8"), encoding="utf-8") as fh:
        assert fh.read() == f"#EXTM3U\n#EXTINF:200,Artist - Song\n{better}\n"   # повтор убран
    # скан не считает корзину пропавшими файлами и не возвращает её в библиотеку
    scanner.scan()
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM tracks WHERE path = ?", (trash,)).fetchone()[0] == "trashed"
        assert conn.execute("SELECT COUNT(*) FROM tracks WHERE status = 'ok' AND title = 'Song'").fetchone()[0] == 1
    # второй прогон ничего не делает
    assert merge.apply(log=lambda m: None)["merged"] == 0


def test_restore_and_empty_trash():
    worse, better, _pid = _setup()
    merge.apply(auto=True, log=lambda m: None)
    with db.connect() as conn:
        items = merge.trash_list(conn)
        assert len(items) == 1 and items[0]["from"] == worse and items[0]["exists"]
        with db.transaction(conn):
            merge.restore(conn, items[0]["id"])
    assert os.path.exists(worse) and os.path.exists(worse[:-4] + ".lrc")
    scanner.scan()
    dedupe.find_duplicates()
    with db.connect() as conn:
        rows = conn.execute("SELECT decision FROM match_candidates c JOIN tracks a ON a.id = c.track_a "
                            "JOIN tracks b ON b.id = c.track_b WHERE a.title = 'Song' AND b.title = 'Song'").fetchall()
        assert [r[0] for r in rows] == ["different"], [tuple(r) for r in rows]   # больше не предлагается склеить
    # снова склеить вручную и очистить корзину
    with db.connect() as conn:
        cid = conn.execute("SELECT c.id FROM match_candidates c JOIN tracks a ON a.id = c.track_a "
                           "WHERE a.title = 'Song'").fetchone()[0]
        conn.execute("UPDATE match_candidates SET decision = 'same', applied_at = NULL WHERE id = ?", (cid,))
    assert merge.apply(log=lambda m: None)["merged"] == 1
    with db.connect() as conn, db.transaction(conn):
        res = merge.empty_trash(conn)
    assert res["files"] == 1, res
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks WHERE status = 'trashed'").fetchone()[0] == 0
    assert not os.path.exists(os.path.join(config.MUSIC_DIR, ".trash", "spotify", "Artist", "TIME", "01 - Song.mp3"))


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
