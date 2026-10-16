"""Перенос в индекс того, что сейчас ведёт spotdl (design/plan.md, задача 1.3):

  playlists.txt          → playlists + playlist_sources (по одному источнику Spotify)
  /conf/<id>.spotdl      → playlist_items: состав плейлиста; трек ищется в индексе
                           по ID Spotify, затем по ISRC; не нашёлся — трек «не скачан»
  /conf/errors/<имя>.txt → missing: почему не скачан (нет на YouTube / YouTube не
                           отдал / прочее) и найденная ссылка YouTube

Импорт повторяется после каждого скана индекса — для плейлистов, которые ещё
синкаются по-старому (spotdl sync). Плейлисты, которые синкает сам сервис
(playlists.managed = 1, app/index_sync.py), он не трогает. Список плейлистов
берётся из индекса (с этапа 3 главный он, playlists.txt — выгрузка), поэтому
импорт плейлисты больше не создаёт и не удаляет.

Повторяемый: плейлисты находятся по legacy_id (md5 ссылки, как имена *.spotdl),
состав источника переписывается целиком, «не скачанные» треки находятся по
своему ID Spotify и не плодятся. Плейлист, чьи файлы не менялись с прошлого
импорта, пропускается.
"""
import json
import os
import re
import time
from typing import Dict, List, Optional

from . import config, db, errors_parser, playlists
from .index_sync import REASON_BY_ERROR as _REASON
from .index_sync import add_missing_track, find_track
from .index_sync import spotify_id_of as _spotify_id
from .naming import url_type

_KIND_BY_TYPE = playlists.SOURCE_KIND
_SPOTIFY_ID = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
_YT_URL = re.compile(r"https?://\S*(?:youtube\.com|youtu\.be)\S*")


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _song_from_error(t: dict) -> dict:
    """Строка errors-файла → минимальный «song» (имя «Артист - Название»)."""
    name, artist = t.get("song") or "", ""
    if " - " in name:
        artist, name = name.split(" - ", 1)
    return {"name": name or "?", "artist": artist, "url": t.get("spotify_url")}


def _import_playlist(conn, pl, position: int, now: float, report: dict) -> None:
    kind = _KIND_BY_TYPE.get(url_type(pl.url))
    if not kind:
        report["skipped"].append(f"{pl.name}: неизвестный тип ссылки {pl.url}")
        return
    row = conn.execute("SELECT id, managed FROM playlists WHERE legacy_id = ?", (pl.id,)).fetchone()
    if row and row["managed"]:
        report["playlists_managed"] += 1        # состав ведёт синк через индекс
        return
    if row:
        pid = row[0]
        conn.execute("UPDATE playlists SET name = ?, position = ? WHERE id = ?", (pl.name, position, pid))
    else:
        pid = conn.execute("INSERT INTO playlists(name, position, legacy_id, created_at) VALUES (?, ?, ?, ?)",
                           (pl.name, position, pl.id, now)).lastrowid
        report["playlists_new"] += 1
    ref = "saved" if kind == "spotify_liked" else pl.url
    conn.execute("INSERT OR IGNORE INTO playlist_sources(playlist_id, kind, ref) VALUES (?, ?, ?)",
                 (pid, kind, ref))
    sid = conn.execute("SELECT id FROM playlist_sources WHERE playlist_id = ? AND kind = ? AND ref = ?",
                       (pid, kind, ref)).fetchone()[0]

    # что spotdl не смог скачать в последний синк
    errors = {}
    for t in errors_parser.parse_errors_file(config.errors_path(pl.safe))["tracks"]:
        m = _SPOTIFY_ID.search(t.get("spotify_url") or "")
        if m:
            errors[m.group(1)] = t

    try:
        with open(config.savefile_path(pl.id), "r", encoding="utf-8") as fh:
            songs = playlists.savefile_songs(json.load(fh))
    except (OSError, ValueError):
        songs = []
    st = {"tracks": 0, "linked": 0, "missing": 0, "unexplained": 0}
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND source_id = ?", (pid, sid))
    seen = set()
    entries = [(s, _spotify_id(s)) for s in songs if isinstance(s, dict)]
    # треки, которые есть только в errors-файле (save-файла нет/он старее)
    known = {sp for _s, sp in entries if sp}
    entries += [(_song_from_error(t), sp) for sp, t in errors.items() if sp not in known]
    for pos, (song, sp) in enumerate(entries):
        if sp and sp in seen:
            continue
        seen.add(sp)
        st["tracks"] += 1
        tid = find_track(conn, sp, song.get("isrc"))
        if tid is None:
            tid = add_missing_track(conn, song, sp, now, origin="savefile")
        elif sp:
            # нашёлся по ISRC — запомнить и ID Spotify: синк через индекс получает
            # списки без ISRC и находит трек только по нему
            conn.execute("INSERT OR IGNORE INTO track_ids(kind, value, track_id, origin) "
                         "VALUES ('spotify', ?, ?, 'savefile')", (sp, tid))
        status = conn.execute("SELECT status FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, source_id, track_id, position, added_at) "
                     "VALUES (?, ?, ?, ?, ?)", (pid, sid, tid, pos, now))
        if status == "ok":
            st["linked"] += 1
            continue
        st["missing"] += 1
        err = errors.get(sp) if sp else None
        if err:
            reason = _REASON.get(err.get("error_type"), "other")
            yt = err.get("source_url") or (_YT_URL.search(err.get("raw") or "") or [None])[0]
            detail = (err.get("raw") or "").split(" - ", 1)[-1][:300]
        else:
            # в плейлисте есть, файла с этим треком Spotify нет, а в ошибках spotdl
            # его тоже нет — часто spotdl сопоставил трек с уже скачанным файлом
            # другого релиза; разберёт сопоставление (задача 1.4)
            reason, yt, detail = "other", None, "нет файла с этим треком Spotify"
            st["unexplained"] += 1
        conn.execute(
            "INSERT INTO missing(track_id, reason, detail, source_url) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET reason = excluded.reason, detail = excluded.detail, "
            "source_url = COALESCE(excluded.source_url, missing.source_url)",
            (tid, reason, detail, yt))
    result = dict(st, at=now)
    # status (ok / unavailable) ставит синк по ответу площадки — импорт его не трогает
    conn.execute("UPDATE playlist_sources SET last_result = ? WHERE id = ?", (json.dumps(result), sid))
    for k in ("tracks", "linked", "missing", "unexplained"):
        report[k] += st[k]


def _totals(conn) -> dict:
    """Итог по всем плейлистам: сколько разных треков, скачано, не скачано и почему."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    reasons = {r[0]: r[1] for r in conn.execute(
        "SELECT m.reason, COUNT(*) FROM missing m JOIN tracks t ON t.id = m.track_id "
        "WHERE t.status = 'missing' GROUP BY m.reason")}
    return {
        "in_playlists": one("SELECT COUNT(DISTINCT track_id) FROM playlist_items"),
        "downloaded": one("SELECT COUNT(DISTINCT pi.track_id) FROM playlist_items pi "
                          "JOIN tracks t ON t.id = pi.track_id WHERE t.status = 'ok'"),
        "missing": one("SELECT COUNT(*) FROM tracks WHERE status = 'missing'"),
        "missing_by_reason": reasons,
        "files_not_in_playlists": one("SELECT COUNT(*) FROM tracks WHERE status = 'ok' AND id NOT IN "
                                      "(SELECT track_id FROM playlist_items)"),
    }


def import_legacy(db_path: Optional[str] = None, force: bool = False) -> dict:
    """Импорт playlists.txt / save-файлов / errors в индекс. Возвращает отчёт."""
    t0 = time.time()
    report: Dict = {"playlists": 0, "playlists_new": 0, "playlists_unchanged": 0, "playlists_managed": 0,
                    "tracks": 0, "linked": 0, "missing": 0, "unexplained": 0,
                    "orphans_removed": 0, "skipped": []}
    pls = playlists.read_playlists()
    with db.connect(db_path) as conn:
        try:
            stamps = json.loads(db.get_meta(conn, "legacy_import_stamps") or "{}")
        except ValueError:
            stamps = {}
        # файлы библиотеки менялись — связи «трек ↔ файл» надо перепроверить
        lib_stamp = list(conn.execute("SELECT COUNT(*), MAX(updated_at) FROM tracks WHERE status = 'ok'").fetchone())
        new_stamps = {}
        with db.transaction(conn):
            for pos, pl in enumerate(pls):
                report["playlists"] += 1
                stamp = json.dumps([pl.name, pos, _mtime(config.savefile_path(pl.id)),
                                    _mtime(config.errors_path(pl.safe)), lib_stamp])
                new_stamps[pl.id] = stamp
                in_db = conn.execute("SELECT 1 FROM playlists WHERE legacy_id = ?", (pl.id,)).fetchone()
                if not force and in_db and stamps.get(pl.id) == stamp:
                    report["playlists_unchanged"] += 1
                    continue
                _import_playlist(conn, pl, pos, time.time(), report)
            # «не скачанные» треки, которые больше ни в одном плейлисте не нужны
            report["orphans_removed"] = conn.execute(
                "DELETE FROM tracks WHERE status = 'missing' AND id NOT IN "
                "(SELECT track_id FROM playlist_items)").rowcount
            db.set_meta(conn, "legacy_import_stamps", json.dumps(new_stamps))
            report["totals"] = _totals(conn)
            report["seconds"] = round(time.time() - t0, 2)
            db.set_meta(conn, "legacy_import", json.dumps({k: v for k, v in report.items() if k != "skipped"}))
    return report
