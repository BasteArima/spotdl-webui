"""Сквозной тест: тестовая библиотека (tests/make_dev_library.py) → скан →
импорт плейлистов → поиск дублей (app/dedupe.py). Проверяет, что найдены
ровно заложенные дубли, версии (live, ремастер, каверы) не склеены, а «не
скачанный» трек с файлом той же записи связан с файлом.
Запуск: python tests/test_dedupe.py (нужны ffmpeg и spotdl)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

_ROOT = tempfile.mkdtemp(prefix="dedupe-")
os.environ["MUSIC_DIR"] = os.path.join(_ROOT, "music")
os.environ["CONF_DIR"] = os.path.join(_ROOT, "conf")
os.environ["PLAYLISTS_M3U_DIR"] = os.path.join(_ROOT, "music", "playlists")
os.environ["ERRORS_DIR"] = os.path.join(_ROOT, "conf", "errors")
os.environ["PLAYLISTS_FILE"] = os.path.join(_ROOT, "conf", "playlists.txt")
os.environ["AUTH_ENABLED"] = "false"          # API-тесты без входа
os.environ["AUTOSYNC_INTERVAL_HOURS"] = "0"

from app import db, dedupe, legacy_import, scanner  # noqa: E402
from app.naming import playlist_id  # noqa: E402


def _build():
    subprocess.run([sys.executable, os.path.join(_HERE, "make_dev_library.py"), "--out", _ROOT],
                   check=True, cwd=os.path.dirname(_HERE), env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                   capture_output=True)
    # «не скачан» другим релизом: ID Spotify и ISRC свои, но название, исполнитель
    # и длительность — как у скачанного файла MGMT (spotdl сопоставил с ним)
    path = os.path.join(os.environ["CONF_DIR"], f"{playlist_id('saved')}.spotdl")
    with open(path, encoding="utf-8") as fh:
        songs = json.load(fh)
    songs.append({"name": "Little Dark Age (Official Video)", "artists": ["MGMT"], "artist": "MGMT",
                  "album_name": "Hits", "song_id": "z" * 22, "url": "https://open.spotify.com/track/" + "z" * 22,
                  "isrc": "USSM11799999", "duration": 301})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(songs, fh)


def _pairs(conn):
    return {(r["ta"], r["tb"], r["level"]) for r in conn.execute(
        "SELECT a.title ta, b.title tb, m.level FROM match_candidates m JOIN tracks a ON a.id = m.track_a "
        "JOIN tracks b ON b.id = m.track_b")}


def test_library_duplicates_found_versions_kept_apart():
    _build()
    scanner.scan()
    legacy_import.import_legacy()
    rep = dedupe.find_duplicates()
    with db.connect() as conn:
        pairs = {frozenset((a, b)): lvl for a, b, lvl in _pairs(conn)}
        assert pairs == {
            frozenset(("Hurt",)): 1,                                    # два релиза Hurt — один ролик
            frozenset(("Enemy - from the series Arcane League of Legends",
                       "Enemy (with JID) - from the series Arcane League of Legends")): 1,
            frozenset(("Iris",)): 3,                                    # перевыпуск: ISRC другой
        }, pairs
        titles_in_pairs = {t for p in pairs for t in p}
        for version in ("Hurt - Live", "Iris - Remastered 2023", "Golden (Russian Cover)",
                        "Weight of the World - English Version", "Lagtrain - Russian ver."):
            assert version not in titles_in_pairs, version

        # «не скачанный» релиз MGMT связан с файлом; «Slowed» — нет (другая версия)
        st = {r["title"]: r["status"] for r in conn.execute("SELECT title, status FROM tracks")}
        assert "Little Dark Age (Official Video)" not in st, st     # слит с файлом
        assert st["Little Dark Age - Slowed"] == "missing"
        mgmt = conn.execute("SELECT id FROM tracks WHERE title = 'Little Dark Age'").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM track_ids WHERE track_id = ? AND value = ?",
                            (mgmt, "z" * 22)).fetchone()[0] == 1       # ID релиза теперь у файла
        assert rep["missing_linked"] == 1, rep


def test_rerun_is_stable_and_keeps_decisions():
    _build()
    scanner.scan()
    legacy_import.import_legacy()
    dedupe.find_duplicates()
    with db.connect() as conn:
        before = _pairs(conn)
        conn.execute("UPDATE match_candidates SET decision = 'different', decided_at = 1 WHERE level = 3")
    rep = dedupe.find_duplicates()
    assert rep["candidates_new"] == 0 and rep["missing_linked"] == 0, rep
    with db.connect() as conn:
        assert _pairs(conn) == before
        assert conn.execute("SELECT decision FROM match_candidates WHERE level = 3").fetchone()[0] == "different"
    # импорт после связывания не «отвязывает» трек обратно
    legacy_import.import_legacy(force=True)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks WHERE title = 'Little Dark Age (Official Video)'").fetchone()[0] == 0


def test_home_summary():
    from app import library_queries
    _build()
    scanner.scan()
    legacy_import.import_legacy()
    dedupe.find_duplicates()
    with db.connect() as conn:
        h = library_queries.home(conn)
    assert h["library"]["files"] == 16 and h["library"]["playlists"] == 4, h["library"]
    assert h["sources"]["spotify"]["playlists"] == 3, h["sources"]
    assert h["sources"]["spotify"]["liked"]["total"] == 9, h["sources"]      # 10 в save-файле, MGMT связан
    assert h["sources"]["manual"]["tracks"] == 1, h["sources"]               # Opus 23 вне плейлистов
    a = h["attention"]
    assert a["missing"] == 3 and a["dupes"] == 3 and a["dupes_by_level"] == {1: 2, 3: 1}, a
    assert len(h["recent"]) == 6 and all(t["platforms"] for t in h["recent"]), h["recent"]


def test_playlist_queries_and_fetch_api():
    from fastapi.testclient import TestClient
    from app import jobs, library_queries, main
    _build()
    scanner.scan()
    legacy_import.import_legacy()
    dedupe.find_duplicates()
    with db.connect() as conn:
        pls = {p["name"]: p for p in library_queries.playlists(conn)}
        liked = library_queries.playlist(conn, pls["Любимые"]["id"])
    assert set(pls) == {"Любимые", "Для работы", "Arcane", "Каверы"}, set(pls)
    assert pls["Любимые"]["total"] == 9 and pls["Любимые"]["missing"] == 2, pls["Любимые"]
    assert pls["Любимые"]["sources"][0]["kind"] == "spotify_liked"
    titles = [t["title"] for t in liked["tracks"]]
    assert titles[0] == "Hurt" and len(titles) == 9, titles                   # порядок как в Spotify
    seven = next(t for t in liked["tracks"] if t["title"].startswith("Seven"))
    assert seven["status"] == "missing" and seven["missing"]["reason"] == "not_found" and seven["spotify_url"]

    enqueued = []
    orig = (jobs.enqueue_download_one, jobs.enqueue_auto)
    jobs.enqueue_download_one = lambda *a, **k: enqueued.append(("yt", a)) or jobs.Job("download", "t", lambda j: None)
    jobs.enqueue_auto = lambda *a, **k: enqueued.append(("auto", a)) or jobs.Job("fetch", "t", lambda j: None)
    try:
        c = TestClient(main.app)
        url = f"/api/v2/tracks/{seven['id']}/fetch"
        r = c.post(url, json={"source": "youtube", "url": "not a link"})
        assert r.status_code == 400 and "YouTube" in r.json()["detail"], r.json()
        r = c.post(url, json={"source": "deezer"})
        assert r.status_code == 400 and "Deezer" in r.json()["detail"], r.json()   # ARL не настроен
        r = c.post(url, json={"source": "auto"})
        assert r.status_code == 400 and "Нет источников" in r.json()["detail"], r.json()
        r = c.post(url, json={"source": "youtube", "url": "https://music.youtube.com/watch?v=QU9c0053UAU",
                              "playlist_id": pls["Любимые"]["id"]})
        assert r.status_code == 200, r.json()
        kind, args = enqueued[-1]
        assert kind == "yt" and args[0] == seven["spotify_url"] and args[2] == "Любимые", args
        assert c.get("/api/v2/playlists/999999").status_code == 404
    finally:
        jobs.enqueue_download_one, jobs.enqueue_auto = orig


def test_spotdl_silent_failure_is_not_success():
    from app import jobs
    silent = ["Processing query: a|b", "Error occurred while reinitializing song: 'uri'"]
    ok = ["A - B: Done", 'Downloaded "A - B": https://music.youtube.com/watch?v=x']
    assert jobs.looks_failed(silent) or not jobs.looks_downloaded(silent)
    assert not jobs.looks_failed(ok) and jobs.looks_downloaded(ok)
    assert jobs.looks_downloaded(["Skipping A - B (file already exists) "])


def test_cli_prints_dupes_line():
    _build()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "app.scanner", "--full"], cwd=os.path.dirname(_HERE),
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[dupes] пар проверено" in r.stdout and "тот же ролик/трек 2" in r.stdout, r.stdout
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert last["dedupe"]["candidates"]["1"] == 2, last["dedupe"]


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
