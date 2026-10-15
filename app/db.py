"""Индекс библиотеки — SQLite в /conf/library.db (см. design/plan.md, §3).

Сервис ведёт библиотеку сам: какие файлы есть, откуда каждый трек (Spotify,
YouTube, Deezer, вручную), из каких плейлистов, что не скачано и что похоже на
дубль. spotdl остаётся одним из загрузчиков.

Устройство:
  • Соединение — короткоживущее, на операцию (`with db.connect() as conn:`).
    Веб, планировщик и подпроцессы (сканер) открывают свои — это дёшево, а
    SQLite в режиме WAL даёт читать, пока кто-то пишет.
  • Схема — по номерам миграций в PRAGMA user_version. Новая миграция =
    новая запись в конец MIGRATIONS; старые не править (у пользователя они
    уже применены). Каждая применяется в своей транзакции.
  • Только стандартная библиотека Python: базе не нужен сервер и пакеты.

Время везде — unix-секунды (REAL), как в остальном сервисе.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple

from . import config

# (номер, описание, SQL). Номера — подряд с 1.
MIGRATIONS: List[Tuple[int, str, str]] = [
    (1, "индекс библиотеки: треки, внешние ID, плейлисты, дубли, недокачанное", """
-- Одна запись (recording) — один ряд. path пуст, пока трек не скачан:
-- так «не скачанное» живёт в той же таблице и связано с плейлистами.
CREATE TABLE tracks (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE,                 -- абсолютный путь к файлу или NULL
    status        TEXT NOT NULL DEFAULT 'ok'   -- ok | missing | trashed
                  CHECK (status IN ('ok', 'missing', 'trashed')),
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL DEFAULT '',    -- основной исполнитель
    artists       TEXT NOT NULL DEFAULT '[]',  -- JSON-список всех исполнителей
    album         TEXT NOT NULL DEFAULT '',
    album_artist  TEXT NOT NULL DEFAULT '',
    track_number  INTEGER,
    disc_number   INTEGER,
    year          INTEGER,
    duration      REAL,                        -- секунды
    isrc          TEXT,
    codec         TEXT,                        -- mp3, m4a, opus…
    bitrate       INTEGER,                     -- кбит/с
    file_size     INTEGER,
    file_mtime    REAL,                        -- для инкрементального скана
    norm_key      TEXT,                        -- нормализованные «исполнитель|название» (сопоставление)
    added_at      REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX tracks_isrc ON tracks(isrc) WHERE isrc IS NOT NULL;
CREATE INDEX tracks_norm ON tracks(norm_key) WHERE norm_key IS NOT NULL;
CREATE INDEX tracks_status ON tracks(status);

-- Внешние ID трека: spotify:<id>, youtube:<videoId>, deezer:<id>.
-- Один внешний ID указывает ровно на один трек — по нему и ловятся дубли.
CREATE TABLE track_ids (
    kind      TEXT NOT NULL CHECK (kind IN ('spotify', 'youtube', 'deezer')),
    value     TEXT NOT NULL,
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    origin    TEXT NOT NULL DEFAULT '',        -- откуда узнали: tag, savefile, sync, search…
    PRIMARY KEY (kind, value)
);
CREATE INDEX track_ids_track ON track_ids(track_id);

-- Плейлисты сервиса. legacy_id — id записи из playlists.txt (md5 ссылки),
-- чтобы импорт был повторяемым и старые файлы (*.spotdl, errors) находились.
CREATE TABLE playlists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    auto_sync   INTEGER NOT NULL DEFAULT 1,
    position    INTEGER NOT NULL DEFAULT 0,
    legacy_id   TEXT UNIQUE,
    created_at  REAL NOT NULL
);

-- Откуда плейлист берёт список треков; у одного плейлиста их может быть несколько.
CREATE TABLE playlist_sources (
    id            INTEGER PRIMARY KEY,
    playlist_id   INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('spotify_playlist', 'spotify_album',
                      'spotify_artist', 'spotify_track', 'spotify_liked',
                      'ytm_playlist', 'ytm_liked')),
    ref           TEXT NOT NULL,               -- ссылка / ID на площадке / 'saved'
    label         TEXT NOT NULL DEFAULT '',    -- название на площадке
    last_sync_at  REAL,
    last_result   TEXT,                        -- JSON-итог последнего синка
    status        TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'unavailable', 'error')),
    UNIQUE (playlist_id, kind, ref)
);

-- Состав плейлиста. source_id = 0 — добавлено вручную (не из источника).
-- Один трек из двух источников — две строки; m3u берёт трек один раз.
CREATE TABLE playlist_items (
    playlist_id  INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    source_id    INTEGER NOT NULL DEFAULT 0,
    track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL DEFAULT 0,
    added_at     REAL NOT NULL,
    PRIMARY KEY (playlist_id, source_id, track_id)
);
CREATE INDEX playlist_items_track ON playlist_items(track_id);

-- Возможные дубли: пара треков (меньший id первым), насколько похожи и почему,
-- решение пользователя (same — склеить, different — разные записи).
CREATE TABLE match_candidates (
    id          INTEGER PRIMARY KEY,
    track_a     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    track_b     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    score       INTEGER NOT NULL,              -- 0..100
    level       INTEGER NOT NULL,              -- уровень сопоставления (plan.md, §4)
    reason      TEXT NOT NULL DEFAULT '',
    decision    TEXT CHECK (decision IN ('same', 'different')),
    decided_at  REAL,
    created_at  REAL NOT NULL,
    CHECK (track_a < track_b),
    UNIQUE (track_a, track_b)
);

-- Почему трек не скачан и что уже пробовали (вместо errors/*.txt).
CREATE TABLE missing (
    track_id         INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    reason           TEXT NOT NULL CHECK (reason IN ('not_found', 'download_failed',
                         'network', 'unavailable', 'other')),
    detail           TEXT NOT NULL DEFAULT '',
    source_url       TEXT,                     -- найденная ссылка (YouTube), если была
    tried            TEXT NOT NULL DEFAULT '[]',  -- JSON: какие загрузчики пробовали
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  REAL
);

-- Служебные значения: время последнего скана и т.п.
CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""),
    (2, "плейлисты, которые синкает сервис сам (этап 3)", """
-- managed = 1: состав плейлиста ведёт синк через индекс, m3u собирает сервис.
-- 0 — по-старому: spotdl sync, m3u и save-файл пишет он, индекс их импортирует.
ALTER TABLE playlists ADD COLUMN managed INTEGER NOT NULL DEFAULT 0;
"""),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]

_migrate_lock = threading.Lock()


def _open(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # isolation_level=None: транзакции явные (BEGIN … COMMIT в transaction()),
    # без неявных BEGIN модуля sqlite3, которые путаются с DDL миграций
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Применить недостающие миграции. Возвращает итоговую версию схемы.
    Повторный вызов на актуальной базе ничего не делает."""
    # WAL — постоянное свойство файла базы: веб читает, пока сканер пишет
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"база {current}-й версии новее кода ({SCHEMA_VERSION}) — "
                           "обновите образ, а не откатывайте")
    for num, _title, sql in MIGRATIONS:
        if num <= current:
            continue
        # executescript сам коммитит открытую транзакцию — поэтому BEGIN внутри
        # скрипта: DDL и номер версии применяются вместе или не применяются
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {num};\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")   # ни таблиц наполовину, ни сдвинутой версии
            raise
        current = num
    return current


@contextmanager
def connect(path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Соединение с индексом; если схема отстаёт (новая база, обновили образ,
    файл удалили на ходу) — сначала миграции. Закрывается на выходе из with."""
    path = path or config.LIBRARY_DB
    conn = _open(path)
    try:
        # одна PRAGMA на соединение — дешевле, чем помнить «уже мигрировали»,
        # и переживает удалённый/подменённый файл базы
        if schema_version(conn) != SCHEMA_VERSION:
            with _migrate_lock:
                migrate(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT (или ROLLBACK при исключении). IMMEDIATE сразу
    берёт блокировку записи: два писателя не упрутся в deadlock посреди работы."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: Optional[str]) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def merge_tracks(conn: sqlite3.Connection, from_id: int, into_id: int) -> None:
    """Слить трек from_id в into_id: плейлисты и внешние ID переходят на into,
    from удаляется (с его записью «не скачан» и кандидатами в дубли). Файл
    from_id не трогается — это забота вызывающего (корзина и т.п.)."""
    if from_id == into_id:
        return
    conn.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, source_id, track_id, position, added_at) "
                 "SELECT playlist_id, source_id, ?, position, added_at FROM playlist_items WHERE track_id = ?",
                 (into_id, from_id))
    conn.execute("UPDATE track_ids SET track_id = ? WHERE track_id = ?", (into_id, from_id))
    conn.execute("DELETE FROM tracks WHERE id = ?", (from_id,))


def stats(conn: sqlite3.Connection) -> dict:
    """Сводка для UI и отчётов: сколько треков по статусам, плейлистов, дублей."""
    by_status = {r["status"]: r["n"] for r in
                 conn.execute("SELECT status, COUNT(*) AS n FROM tracks GROUP BY status")}
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "tracks": by_status.get("ok", 0),
        "missing": by_status.get("missing", 0),
        "trashed": by_status.get("trashed", 0),
        "playlists": one("SELECT COUNT(*) FROM playlists"),
        "dupes_open": one("SELECT COUNT(*) FROM match_candidates WHERE decision IS NULL"),
    }
