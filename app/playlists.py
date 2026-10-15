"""Плейлисты сервиса.

Главный список — в индексе (/conf/library.db, таблицы playlists и
playlist_sources; design/plan.md, задача 3.3). При первом обращении после
обновления он один раз переносится из playlists.txt. Дальше playlists.txt —
только выгрузка: сервис переписывает его после каждой правки, чтобы прежний
синк через spotdl, прежний интерфейс (/legacy) и откат на старый образ видели
тот же список. Правки руками в файле больше не подхватываются.

Формат файла — `Имя|ссылка`, разбор как у bash автосинка:
    name = до первого '|', с обрезкой ХВОСТОВЫХ пробелов
    url  = всё после первого '|', с обрезкой НАЧАЛЬНЫХ пробелов
Строки, начинающиеся с '#', и пустые — игнорируются.
"""
import os
import tempfile
import threading
import time
from typing import List, Optional

from . import config
from .naming import SAVED_QUERY, is_saved, playlist_id, safe_name, url_type

# тип ссылки → вид источника в индексе
SOURCE_KIND = {"playlist": "spotify_playlist", "album": "spotify_album", "artist": "spotify_artist",
               "track": "spotify_track", "saved": "spotify_liked"}
_MASTER_KEY = "playlists_master"
_master_lock = threading.Lock()


class Playlist:
    def __init__(self, name: str, url: str, raw_index: int):
        self.name = name
        self.url = url
        self.raw_index = raw_index  # индекс непустой/незакомментированной записи

    @property
    def safe(self) -> str:
        return safe_name(self.name)

    @property
    def id(self) -> str:
        return playlist_id(self.url)

    @property
    def type(self) -> str:
        return url_type(self.url)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "safe": self.safe,
            "id": self.id,
            "type": self.type,
        }


def savefile_songs(data) -> list:
    """Треки из save-файла spotdl (`/conf/<id>.spotdl`). Форматов два:
    `sync --save-file` пишет {"type": "sync", "query": [...], "songs": [...]},
    `download --save-file` (так синкаются Liked Songs) — просто список треков."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("songs") or []
    return []


def _parse_line(line: str) -> Optional[tuple]:
    """Возвращает (name, url) либо None для пустых/комментариев/без '|'."""
    if not line:
        return None
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "|" not in line:
        return None
    name_part, url_part = line.split("|", 1)
    name = name_part.rstrip()          # trim trailing whitespace
    url = url_part.strip()             # trim leading (и хвостовой \n) whitespace
    if not url:
        return None
    return name, url


def read_playlists_file() -> List[Playlist]:
    """Записи playlists.txt как есть (для переноса в индекс)."""
    path = config.PLAYLISTS_FILE
    result: List[Playlist] = []
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as fh:
        idx = 0
        for line in fh:
            parsed = _parse_line(line.rstrip("\n"))
            if parsed is None:
                continue
            name, url = parsed
            result.append(Playlist(name, url, idx))
            idx += 1
    return result


# ------------------------------------------------------------------ индекс — главный список
def _source_ref(url: str) -> str:
    return SAVED_QUERY if is_saved(url) else url


def ensure_db_master(conn=None) -> None:
    """Один раз перенести playlists.txt в индекс; дальше главный — индекс.
    Порядок и имена берутся из файла; плейлисты, уже импортированные раньше
    (по legacy_id), не дублируются."""
    from . import db
    if conn is None:
        with db.connect() as c:
            return ensure_db_master(c)
    if db.get_meta(conn, _MASTER_KEY) == "db":
        return
    with _master_lock, db.transaction(conn):
        if db.get_meta(conn, _MASTER_KEY) == "db":
            return
        now = time.time()
        for pos, pl in enumerate(read_playlists_file()):
            kind = SOURCE_KIND.get(pl.type)
            if not kind:
                print(f"[playlists] пропущен при переносе: «{pl.name}» — непонятная ссылка {pl.url}", flush=True)
                continue
            row = conn.execute("SELECT id FROM playlists WHERE legacy_id = ?", (pl.id,)).fetchone()
            if row:
                pid = row[0]
                conn.execute("UPDATE playlists SET name = ?, position = ? WHERE id = ?", (pl.name, pos, pid))
            else:
                pid = conn.execute("INSERT INTO playlists(name, position, legacy_id, created_at) "
                                   "VALUES (?, ?, ?, ?)", (pl.name, pos, pl.id, now)).lastrowid
            conn.execute("INSERT OR IGNORE INTO playlist_sources(playlist_id, kind, ref) VALUES (?, ?, ?)",
                         (pid, kind, _source_ref(pl.url)))
        db.set_meta(conn, _MASTER_KEY, "db")


def _rows(conn) -> list:
    """(id, name, ссылка) плейлистов с источником Spotify — по порядку."""
    return [(r["id"], r["name"], r["ref"]) for r in conn.execute(
        "SELECT p.id, p.name, s.ref FROM playlists p JOIN playlist_sources s ON s.id = "
        "(SELECT MIN(id) FROM playlist_sources WHERE playlist_id = p.id AND kind LIKE 'spotify_%') "
        "ORDER BY p.position, p.id")]


def read_playlists() -> List[Playlist]:
    """Плейлисты с источником Spotify — из индекса, в порядке сервиса."""
    from . import db
    with db.connect() as conn:
        ensure_db_master(conn)
        return [Playlist(name, url, i) for i, (_pid, name, url) in enumerate(_rows(conn))]


def db_id(conn, url: str) -> Optional[int]:
    """id плейлиста в индексе по ссылке его источника Spotify."""
    for pid, _name, ref in _rows(conn):
        if ref == url:
            return pid
    return None


def export_file(conn=None) -> None:
    """Переписать playlists.txt по индексу (для прежнего синка и /legacy)."""
    from . import db
    if conn is None:
        with db.connect() as c:
            return export_file(c)
    lines = ["# Список ведёт spotdl-webui (раздел «Плейлисты»); правки здесь будут перезаписаны."]
    lines += [f"{name}|{url}" for _pid, name, url in _rows(conn)]
    _write_all(lines)


def _check(name: str, url: str) -> tuple:
    name = name.rstrip()
    url = _normalize_url(url)
    if not name or not url:
        raise ValueError("Имя и ссылка обязательны")
    if "|" in name:
        raise ValueError("Имя не может содержать символ '|'")
    if url_type(url) not in SOURCE_KIND:
        raise ValueError("Нужна ссылка Spotify на плейлист, альбом, исполнителя или трек — "
                         "или saved для «Любимых треков»")
    return name, url


def _drop_orphans(conn) -> None:
    """«Не скачанные» треки, которые больше ни одному плейлисту не нужны."""
    conn.execute("DELETE FROM tracks WHERE status = 'missing' AND id NOT IN (SELECT track_id FROM playlist_items)")


def _write_all(lines: List[str]) -> None:
    """Атомарная перезапись playlists.txt (сохраняем перевод строки в конце)."""
    path = config.PLAYLISTS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".playlists.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _normalize_url(url: str) -> str:
    """`Saved`/` saved ` → канонический `saved`: от строки считается id
    save-файла, поэтому написание должно быть одно."""
    url = url.strip()
    return SAVED_QUERY if is_saved(url) else url


def add_playlist(name: str, url: str) -> Playlist:
    from . import db
    name, url = _check(name, url)
    with db.connect() as conn:
        ensure_db_master(conn)
        with db.transaction(conn):
            if db_id(conn, url) is not None:
                raise ValueError("Плейлист с такой ссылкой уже есть")
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM playlists").fetchone()[0]
            row = conn.execute("SELECT id FROM playlists WHERE legacy_id = ?", (playlist_id(url),)).fetchone()
            if row:
                # ряд от прошлой жизни этой ссылки (без источника) — берём его
                pid = row[0]
                conn.execute("UPDATE playlists SET name = ?, position = ? WHERE id = ?", (name, pos, pid))
            else:
                pid = conn.execute("INSERT INTO playlists(name, position, legacy_id, created_at) "
                                   "VALUES (?, ?, ?, ?)", (name, pos, playlist_id(url), time.time())).lastrowid
            conn.execute("INSERT OR IGNORE INTO playlist_sources(playlist_id, kind, ref) VALUES (?, ?, ?)",
                         (pid, SOURCE_KIND[url_type(url)], _source_ref(url)))
        export_file(conn)
        return Playlist(name, url, len(_rows(conn)) - 1)


def update_playlist(old_url: str, name: str, url: str) -> Playlist:
    from . import db
    name, url = _check(name, url)
    with db.connect() as conn:
        ensure_db_master(conn)
        with db.transaction(conn):
            pid = db_id(conn, old_url)
            if pid is None:
                raise ValueError("Запись не найдена")
            if url != old_url:
                if db_id(conn, url) is not None:
                    raise ValueError("Плейлист с такой ссылкой уже есть")
                # другая ссылка — другой состав: старый уходит, новый придёт синком
                src = conn.execute("SELECT id FROM playlist_sources WHERE playlist_id = ? AND ref = ?",
                                   (pid, old_url)).fetchone()[0]
                conn.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND source_id = ?", (pid, src))
                conn.execute("UPDATE playlist_sources SET kind = ?, ref = ?, label = '', last_sync_at = NULL, "
                             "last_result = NULL, status = 'ok' WHERE id = ?",
                             (SOURCE_KIND[url_type(url)], _source_ref(url), src))
                conn.execute("UPDATE playlists SET legacy_id = ? WHERE id = ?", (playlist_id(url), pid))
                _drop_orphans(conn)
            conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, pid))
        export_file(conn)
        idx = [r[0] for r in _rows(conn)].index(pid)
        return Playlist(name, url, idx)


def delete_playlist(url: str) -> bool:
    from . import db
    with db.connect() as conn:
        ensure_db_master(conn)
        with db.transaction(conn):
            pid = db_id(conn, url)
            if pid is None:
                return False
            conn.execute("DELETE FROM playlists WHERE id = ?", (pid,))
            _drop_orphans(conn)
        export_file(conn)
    return True


def find_by_safe(safe: str) -> Optional[Playlist]:
    """Найти плейлист, safe-имя которого совпадает с safe (имя errors-файла)."""
    for p in read_playlists():
        if p.safe == safe:
            return p
    return None


def find_by_url(url: str) -> Optional[Playlist]:
    for p in read_playlists():
        if p.url == url:
            return p
    return None
