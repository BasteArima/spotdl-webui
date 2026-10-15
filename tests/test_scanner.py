"""Тесты сканера (app/scanner.py) на настоящих файлах: тишина из ffmpeg,
теги — тем же embed_metadata, что у spotdl и нашего place_file.
Запуск: python tests/test_scanner.py (нужны ffmpeg и spotdl)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = tempfile.mkdtemp(prefix="scan-")
os.environ["MUSIC_DIR"] = os.path.join(_ROOT, "music")
os.environ["CONF_DIR"] = os.path.join(_ROOT, "conf")
os.environ["PLAYLISTS_M3U_DIR"] = os.path.join(_ROOT, "music", "playlists")

from app import config, db, scanner  # noqa: E402

MUSIC = config.MUSIC_DIR


def _song(name, artists, album_artist, sp_id, yt=None, isrc="QZABC2400001", album="Album"):
    from spotdl.types.song import Song
    return Song.from_missing_data(
        name=name, artists=artists, artist=artists[0], album_name=album, album_artist=album_artist,
        album_id="al", genres=[], disc_number=1, disc_count=1, duration=2, year=2024,
        date="2024-01-01", track_number=3, tracks_count=12, song_id=sp_id,
        url=f"https://open.spotify.com/track/{sp_id}", isrc=isrc,
        download_url=f"https://music.youtube.com/watch?v={yt}" if yt else None,
        cover_url=None, explicit=False, publisher="", copyright_text=None, lyrics=None)


def _make(rel, song=None, fmt="mp3"):
    from spotdl.utils.metadata import embed_metadata
    from pathlib import Path
    path = os.path.join(MUSIC, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], "m4a": ["-c:a", "aac", "-b:a", "128k"],
             "opus": ["-c:a", "libopus", "-b:a", "96k"], "flac": ["-c:a", "flac"]}[fmt]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "2", *codec, path], check=True)
    if song:
        embed_metadata(Path(path), song, skip_album_art=True)
    return os.path.abspath(path)


def _build_library():
    shutil.rmtree(MUSIC, ignore_errors=True)
    files = {
        "huntrx": _make("spotify/HUNTR X/KPop Demon Hunters/03 - Golden.mp3",
                        _song("Golden", ["HUNTR/X", "EJAE", "AUDREY NUNA"], "HUNTR/X",
                              "1CPZ5BxNNd0n0nF4Orb9JS", yt="yebNIHKAC4A", isrc="USA2P2525423")),
        "m4a": _make("spotify/Tycho/Awake/01 - Awake.m4a",
                     _song("Awake", ["Tycho"], "Tycho", "2aiuhnDY8tOHB2Q0OQi4oK", isrc="USGHI1300001"), "m4a"),
        "opus": _make("spotify/Kai Engel/Sustains/02 - Highway.opus",
                      _song("Highway to the Stars", ["Kai Engel"], "Kai Engel", "3bnuhnDY8tOHB2Q0OQi4oK",
                            yt="AAAAAAAAAAA", isrc="FR10S1500001"), "opus"),
        "flac": _make("upload/Lumiere.flac",
                      _song("Opus 23", ["Dustin O'Halloran"], "Dustin O'Halloran", "4cnuhnDY8tOHB2Q0OQi4oK", isrc="DEUM71500002"), "flac"),
        # тот же трек Spotify вторым файлом — общий внешний ID
        "dup": _make("spotify/HUNTR X/Other/03 - Golden.mp3",
                     _song("Golden", ["HUNTR/X"], "HUNTR/X", "1CPZ5BxNNd0n0nF4Orb9JS")),
        "notags": _make("misc/Some Artist - Untagged.mp3"),
    }
    broken = os.path.join(MUSIC, "misc", "broken.mp3")
    with open(broken, "wb") as fh:
        fh.write(b"not really an mp3" * 10)
    files["broken"] = os.path.abspath(broken)
    # не индексируются: m3u-папка, корзина, скрытые, не-аудио
    _make("playlists/inside-playlists.mp3")
    _make(".trash/old.mp3")
    os.makedirs(os.path.join(MUSIC, "spotify"), exist_ok=True)
    open(os.path.join(MUSIC, "spotify", "cover.jpg"), "wb").close()
    return files


def _row(conn, path):
    return conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()


def _ids(conn, tid):
    return {r["kind"]: r["value"] for r in conn.execute("SELECT kind, value FROM track_ids WHERE track_id = ?", (tid,))}


def test_scan_reads_tags_ids_and_skips_service_dirs():
    files = _build_library()
    if os.path.exists(config.LIBRARY_DB):
        os.remove(config.LIBRARY_DB)
    res = scanner.scan()
    assert res["files"] == 7 and res["new"] == 7, res
    assert res["unreadable"] == 1, res                     # broken.mp3 — но в индексе есть
    assert res["id_conflicts"] == 1, res                   # два файла Golden с одним Spotify ID
    with db.connect() as conn:
        g = _row(conn, files["huntrx"])
        assert g["title"] == "Golden" and g["album"] == "Album", dict(g)
        assert g["artist"] == "HUNTR/X", g["artist"]          # «/» в имени не разрезан
        assert json.loads(g["artists"]) == ["HUNTR/X", "EJAE", "AUDREY NUNA"], g["artists"]
        assert g["track_number"] == 3 and g["disc_number"] == 1 and g["year"] == 2024
        assert g["isrc"] == "USA2P2525423" and g["codec"] == "mp3" and g["bitrate"]
        assert 1.5 < g["duration"] < 2.5, g["duration"]
        assert _ids(conn, g["id"]).get("youtube") == "yebNIHKAC4A", _ids(conn, g["id"])
        # один Spotify ID на два файла: достаётся тому, кого обошли первым, второй — в конфликт
        owner = conn.execute("SELECT track_id FROM track_ids WHERE kind='spotify' AND value='1CPZ5BxNNd0n0nF4Orb9JS'").fetchone()[0]
        dup_id = _row(conn, files["dup"])["id"]
        assert owner in (g["id"], dup_id), owner
        cand = conn.execute("SELECT * FROM match_candidates").fetchall()
        assert len(cand) == 1 and {cand[0]["track_a"], cand[0]["track_b"]} == {g["id"], dup_id}, [dict(c) for c in cand]
        assert cand[0]["level"] == 1 and "Spotify" in cand[0]["reason"]

        m = _row(conn, files["m4a"])
        assert m["title"] == "Awake" and m["isrc"] is None, dict(m)   # spotdl не пишет ISRC в m4a
        assert _ids(conn, m["id"]).get("spotify") == "2aiuhnDY8tOHB2Q0OQi4oK"

        o = _row(conn, files["opus"])
        assert _ids(conn, o["id"]) == {"spotify": "3bnuhnDY8tOHB2Q0OQi4oK", "youtube": "AAAAAAAAAAA"}, _ids(conn, o["id"])
        assert o["bitrate"], "битрейт opus — по размеру и длительности"

        fl = _row(conn, files["flac"])
        assert fl["title"] == "Opus 23" and _ids(conn, fl["id"]).get("spotify")

        u = _row(conn, files["notags"])
        assert u["title"] == "Some Artist - Untagged" and _ids(conn, u["id"]) == {}

        b = _row(conn, files["broken"])
        assert b is not None and b["title"] == "broken"

        paths = [r["path"] for r in conn.execute("SELECT path FROM tracks")]
        assert not any("playlists" in p or ".trash" in p for p in paths), paths
    assert scanner.last_scan()["files"] == 7


def test_rescan_is_incremental():
    files = _build_library()
    scanner.scan(full=True)
    t0 = time.time()
    res = scanner.scan()
    assert res["unchanged"] == 7 and res["new"] == 0 and res["updated"] == 0, res
    assert time.time() - t0 < 5
    # перетегировали один файл — перечитан только он, старые ID из тегов заменены
    from spotdl.utils.metadata import embed_metadata
    from pathlib import Path
    time.sleep(0.05)
    embed_metadata(Path(files["opus"]), _song("Highway to the Stars", ["Kai Engel"], "Kai Engel",
                                              "5dnuhnDY8tOHB2Q0OQi4oK", isrc="FR10S1500001"), skip_album_art=True)
    res = scanner.scan()
    assert res["updated"] == 1 and res["unchanged"] == 6, res
    with db.connect() as conn:
        o = _row(conn, files["opus"])
        # spotdl при перезаписи не стирает старый комментарий со ссылкой на ролик —
        # он остаётся в файле, и сканер честно его видит
        assert _ids(conn, o["id"])["spotify"] == "5dnuhnDY8tOHB2Q0OQi4oK", _ids(conn, o["id"])
        assert conn.execute("SELECT COUNT(*) FROM track_ids WHERE value = '3bnuhnDY8tOHB2Q0OQi4oK'").fetchone()[0] == 0


def test_vanished_files_removed_or_kept_for_playlists():
    files = _build_library()
    scanner.scan(full=True)
    with db.connect() as conn:
        keep_id = _row(conn, files["m4a"])["id"]
        pl = conn.execute("INSERT INTO playlists(name, created_at) VALUES ('P', 0)").lastrowid
        conn.execute("INSERT INTO playlist_items(playlist_id, track_id, added_at) VALUES (?, ?, 0)", (pl, keep_id))
    os.remove(files["m4a"])          # в плейлисте — станет «не скачан»
    os.remove(files["notags"])       # ни в одном плейлисте — уйдёт из индекса
    res = scanner.scan()
    assert res["vanished"] == 2 and res["kept_missing"] == 1, res
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM tracks WHERE id = ?", (keep_id,)).fetchone()
        assert t["status"] == "missing" and t["path"] is None, dict(t)
        assert conn.execute("SELECT reason FROM missing WHERE track_id = ?", (keep_id,)).fetchone()[0] == "other"
        assert _ids(conn, keep_id).get("spotify")           # ID остались — найдём при следующем синке
        assert _row(conn, files["notags"]) is None


def test_split_artists():
    s = scanner.split_artists
    assert s("A/B/C", "A") == ["A", "B", "C"]
    assert s("HUNTR/X", "HUNTR/X") == ["HUNTR/X"]
    assert s("HUNTR/X/EJAE", "HUNTR/X") == ["HUNTR/X", "EJAE"]
    assert s("AC/DC", "Various Artists") == ["AC", "DC"]   # без подсказки неразличимо — известный предел
    assert s("", "X") == []


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
