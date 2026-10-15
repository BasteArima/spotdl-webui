"""Тесты индекса библиотеки (app/db.py): миграции на пустой и уже созданной
базе, откат, связи и ограничения схемы. Запуск: python tests/test_db.py"""
import os
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402

TABLES = {"tracks", "track_ids", "playlists", "playlist_sources", "playlist_items",
          "match_candidates", "missing", "meta"}


def _tmp_db() -> str:
    d = tempfile.mkdtemp(prefix="libdb-")
    return os.path.join(d, "sub", "library.db")     # папки ещё нет — db создаст


def _fresh_process_state(path: str) -> None:
    """Раньше здесь сбрасывался кэш миграций; теперь connect() сверяет версию сам."""


def _add_track(conn, title="Song", path=None, status="ok", **kw) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO tracks(path, status, title, artist, added_at, updated_at, isrc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (path, status, title, kw.get("artist", "Artist"), now, now, kw.get("isrc")))
    return cur.lastrowid


def test_empty_db_gets_full_schema():
    path = _tmp_db()
    with db.connect(path) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert TABLES <= names, TABLES - names
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.stats(conn) == {"tracks": 0, "missing": 0, "trashed": 0,
                                  "playlists": 0, "dupes_open": 0}


def test_repeated_open_keeps_data_and_version():
    path = _tmp_db()
    with db.connect(path) as conn:
        _add_track(conn, "Hurt", path="/music/a.mp3")
        db.set_meta(conn, "last_scan_at", "123")
    for _ in range(3):
        _fresh_process_state(path)
        with db.connect(path) as conn:
            assert db.schema_version(conn) == db.SCHEMA_VERSION
            assert db.migrate(conn) == db.SCHEMA_VERSION      # и прямой повтор — без ошибок
            assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1
            assert db.get_meta(conn, "last_scan_at") == "123"


def test_failed_migration_rolls_back():
    path = _tmp_db()
    saved = list(db.MIGRATIONS)
    try:
        db.MIGRATIONS[:] = [(1, "ok", "CREATE TABLE a(x);"),
                            (2, "broken", "CREATE TABLE b(x); CREATE TABLE b(x);")]
        conn = db._open(path)
        try:
            db.migrate(conn)
            raise AssertionError("ожидалась ошибка миграции")
        except sqlite3.OperationalError:
            pass
        assert db.schema_version(conn) == 1                   # первая применилась
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "a" in names and "b" not in names, names       # вторая — целиком откатилась
        assert not conn.in_transaction
        conn.close()
    finally:
        db.MIGRATIONS[:] = saved


def test_db_file_deleted_while_running_is_recreated():
    path = _tmp_db()
    with db.connect(path) as conn:
        _add_track(conn, "x")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    with db.connect(path) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_newer_db_than_code_is_refused():
    path = _tmp_db()
    conn = db._open(path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
    try:
        db.migrate(conn)
        raise AssertionError("ожидался отказ")
    except RuntimeError as e:
        assert "новее кода" in str(e)
    finally:
        conn.close()


def test_transaction_commit_and_rollback():
    path = _tmp_db()
    with db.connect(path) as conn:
        with db.transaction(conn):
            _add_track(conn, "kept")
        try:
            with db.transaction(conn):
                _add_track(conn, "dropped")
                raise ValueError("boom")
        except ValueError:
            pass
        titles = [r[0] for r in conn.execute("SELECT title FROM tracks")]
        assert titles == ["kept"], titles


def test_external_id_points_to_one_track_and_cascades():
    path = _tmp_db()
    with db.connect(path) as conn:
        a = _add_track(conn, "A", path="/music/a.mp3")
        b = _add_track(conn, "B", path="/music/b.mp3")
        conn.execute("INSERT INTO track_ids(kind, value, track_id) VALUES ('youtube', 'vid1', ?)", (a,))
        try:
            conn.execute("INSERT INTO track_ids(kind, value, track_id) VALUES ('youtube', 'vid1', ?)", (b,))
            raise AssertionError("один videoId не может указывать на два трека")
        except sqlite3.IntegrityError:
            pass
        conn.execute("DELETE FROM tracks WHERE id = ?", (a,))
        assert conn.execute("SELECT COUNT(*) FROM track_ids").fetchone()[0] == 0


def test_missing_tracks_have_no_path_and_unique_paths():
    path = _tmp_db()
    with db.connect(path) as conn:
        _add_track(conn, "m1", status="missing")
        _add_track(conn, "m2", status="missing")          # несколько NULL-путей — можно
        _add_track(conn, "f", path="/music/x.mp3")
        try:
            _add_track(conn, "f2", path="/music/x.mp3")   # один файл — один трек
            raise AssertionError("ожидался конфликт пути")
        except sqlite3.IntegrityError:
            pass
        try:
            _add_track(conn, "bad", status="weird")
            raise AssertionError("ожидался отказ CHECK по статусу")
        except sqlite3.IntegrityError:
            pass
        assert db.stats(conn)["missing"] == 2


def test_playlist_with_two_sources_and_cascade():
    path = _tmp_db()
    with db.connect(path) as conn:
        now = time.time()
        pl = conn.execute("INSERT INTO playlists(name, created_at) VALUES ('Для работы', ?)", (now,)).lastrowid
        s1 = conn.execute("INSERT INTO playlist_sources(playlist_id, kind, ref) VALUES (?, 'spotify_playlist', 'u1')", (pl,)).lastrowid
        s2 = conn.execute("INSERT INTO playlist_sources(playlist_id, kind, ref) VALUES (?, 'ytm_playlist', 'PL1')", (pl,)).lastrowid
        t = _add_track(conn, "Shared", path="/music/s.mp3")
        for s in (s1, s2):    # один трек из двух источников — две строки, файл один
            conn.execute("INSERT INTO playlist_items(playlist_id, source_id, track_id, added_at) VALUES (?, ?, ?, ?)", (pl, s, t, now))
        distinct = conn.execute("SELECT COUNT(DISTINCT track_id) FROM playlist_items WHERE playlist_id = ?", (pl,)).fetchone()[0]
        assert distinct == 1
        try:
            conn.execute("INSERT INTO playlist_sources(playlist_id, kind, ref) VALUES (?, 'tidal', 'x')", (pl,))
            raise AssertionError("неизвестный вид источника должен отвергаться")
        except sqlite3.IntegrityError:
            pass
        conn.execute("DELETE FROM playlists WHERE id = ?", (pl,))
        assert conn.execute("SELECT COUNT(*) FROM playlist_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1   # файл не трогается


def test_match_candidates_pair_order_and_uniqueness():
    path = _tmp_db()
    with db.connect(path) as conn:
        a, b = _add_track(conn, "A"), _add_track(conn, "B")
        now = time.time()
        conn.execute("INSERT INTO match_candidates(track_a, track_b, score, level, created_at) VALUES (?, ?, 91, 4, ?)", (a, b, now))
        for pair in ((a, b), (b, a)):
            try:
                conn.execute("INSERT INTO match_candidates(track_a, track_b, score, level, created_at) VALUES (?, ?, 50, 4, ?)", (*pair, now))
                raise AssertionError(f"пара {pair} должна отвергаться")
            except sqlite3.IntegrityError:
                pass
        assert db.stats(conn)["dupes_open"] == 1


def test_reader_not_blocked_by_writer():
    """WAL: веб читает, пока сканер держит транзакцию записи."""
    path = _tmp_db()
    with db.connect(path) as conn:
        _add_track(conn, "before", path="/music/a.mp3")
    started, release, result = threading.Event(), threading.Event(), {}

    def writer():
        with db.connect(path) as w:
            with db.transaction(w):
                _add_track(w, "during", path="/music/b.mp3")
                started.set()
                release.wait(5)

    th = threading.Thread(target=writer)
    th.start()
    started.wait(5)
    t0 = time.time()
    with db.connect(path) as r:
        result["n"] = r.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    result["wait"] = time.time() - t0
    release.set()
    th.join(5)
    assert result["n"] == 1, result          # незакоммиченное не видно
    assert result["wait"] < 1.0, result      # и чтение не ждало писателя


if __name__ == "__main__":
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
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failures else 0)
