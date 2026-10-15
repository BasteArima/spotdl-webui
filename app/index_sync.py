"""Синк плейлиста через индекс (design/plan.md, этап 3).

Прежний синк — `spotdl sync <ссылка>`: spotdl сам получает список, решает, что
качать, и пишет m3u, save-файл и errors-файл, а индекс потом их импортирует.
Новый — сервис ведёт библиотеку сам:

  1. список   источник отдаёт треки без загрузки (app/spotify_source.py;
              Liked Songs — app.spotify_login --saved-songs);
  2. сверка   каждый трек ищется в индексе: по ID Spotify, по ISRC, потом по
              названию + исполнителю + длительности (dedupe.link_missing, уровни
              1–3) — так не качается второй раз то, что уже лежит под другим
              релизом; чего нет — ряд «не скачан»;
  3. загрузка `spotdl download <файл>` только по недостающим; обёртка
              app/spotdl_run.py сообщает итог каждого трека строкой
              `@@webui-result {…}` (путь, ролик, ошибка);
  4. итоги    файл → трек получает путь (сканер дочитает теги), ошибка →
              причина в `missing` (нет на YouTube / YouTube не отдал / …);
  5. m3u      собирается из индекса (app/m3u_gen.py) — только скачанное.

Плейлист, прошедший новый синк, помечается playlists.managed = 1: его состав
ведёт индекс, импорт save-/errors-файлов (legacy_import) его не трогает. Синк
по-старому снимает пометку.

Здесь же — общие помощники сопоставления (их использует и legacy_import).
"""
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional

from . import config, db, matching

RESULT_PREFIX = "@@webui-result "        # см. app/spotdl_run.py
_SPOTIFY_ID = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
_YT_URL = re.compile(r"https?://\S*(?:youtube\.com|youtu\.be)\S*")
# класс исключения spotdl → причина «не скачан»
REASON_BY_ERROR = {"LookupError": "not_found", "AudioProviderError": "download_failed",
                   "ConnectionError": "network"}


class ListUnavailable(RuntimeError):
    """Площадка не отдаёт список (персональная подборка, закрытый, удалён)."""


class ListError(RuntimeError):
    """Список не получить по другой причине (сеть, лимит, сбой площадки)."""


# ------------------------------------------------------------------ сопоставление
def spotify_id_of(song: dict) -> Optional[str]:
    sid = song.get("song_id")
    if isinstance(sid, str) and len(sid) == 22:
        return sid
    m = _SPOTIFY_ID.search(song.get("url") or "")
    return m.group(1) if m else None


def find_track(conn, spotify_id: Optional[str], isrc: Optional[str]) -> Optional[int]:
    """Трек индекса по ID Spotify, затем по ISRC (скачанный — первым)."""
    if spotify_id:
        row = conn.execute("SELECT track_id FROM track_ids WHERE kind = 'spotify' AND value = ?",
                           (spotify_id,)).fetchone()
        if row:
            return row[0]
    isrc = (isrc or "").strip().upper()
    if isrc:
        row = conn.execute("SELECT id FROM tracks WHERE isrc = ? ORDER BY status = 'ok' DESC, id LIMIT 1",
                           (isrc,)).fetchone()
        if row:
            return row[0]
    return None


def add_missing_track(conn, song: dict, spotify_id: Optional[str], now: float, origin: str = "sync") -> int:
    """Трек из плейлиста, файла которого в библиотеке нет: ряд без пути."""
    artists = song.get("artists") or ([song["artist"]] if song.get("artist") else [])
    if isinstance(artists, str):
        artists = [artists]
    isrc = (song.get("isrc") or "").strip().upper() or None
    year = song.get("year")
    tid = conn.execute(
        "INSERT INTO tracks(path, status, title, artist, artists, album, album_artist, track_number, "
        "disc_number, year, duration, isrc, added_at, updated_at, norm_key) "
        "VALUES (NULL, 'missing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (song.get("name") or "?", artists[0] if artists else "", json.dumps(artists, ensure_ascii=False),
         song.get("album_name") or "", song.get("album_artist") or "", song.get("track_number"),
         song.get("disc_number"), year if isinstance(year, int) else None,
         song.get("duration"), isrc, now, now, matching.title_key(song.get("name") or ""))).lastrowid
    if spotify_id:
        conn.execute("INSERT OR IGNORE INTO track_ids(kind, value, track_id, origin) VALUES ('spotify', ?, ?, ?)",
                     (spotify_id, tid, origin))
    return tid


def reconcile(conn, playlist_id: int, source_id: int, songs: List[dict], now: float) -> dict:
    """Состав источника по свежему списку: переписывает playlist_items этого
    источника, недостающим заводит ряды «не скачан», связывает их с уже
    лежащими файлами той же записи. Вызывать в транзакции.
    Возвращает {"tracks", "present", "linked", "to_fetch": [(track_id, song)]}."""
    from . import dedupe
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND source_id = ?", (playlist_id, source_id))
    seen, entries = set(), []
    for pos, song in enumerate(s for s in songs if isinstance(s, dict)):
        sp = spotify_id_of(song)
        if sp and sp in seen:
            continue            # трек дважды в одном плейлисте — один раз
        seen.add(sp)
        tid = find_track(conn, sp, song.get("isrc"))
        if tid is None:
            tid = add_missing_track(conn, song, sp, now)
        elif sp:
            # нашёлся по ISRC — запомнить и этот ID Spotify
            conn.execute("INSERT OR IGNORE INTO track_ids(kind, value, track_id, origin) "
                         "VALUES ('spotify', ?, ?, 'sync')", (sp, tid))
        conn.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, source_id, track_id, position, added_at) "
                     "VALUES (?, ?, ?, ?, ?)", (playlist_id, source_id, tid, pos, now))
        entries.append((sp, song))
    missing = [r[0] for r in conn.execute(
        "SELECT t.id FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id "
        "WHERE pi.playlist_id = ? AND pi.source_id = ? AND t.status = 'missing'", (playlist_id, source_id))]
    linked = dedupe.link_missing(conn, missing)
    to_fetch = []
    for sp, song in entries:
        tid = find_track(conn, sp, None) if sp else None
        if tid is None:
            continue
        if conn.execute("SELECT status FROM tracks WHERE id = ?", (tid,)).fetchone()[0] == "missing":
            to_fetch.append((tid, song))
    return {"tracks": len(entries), "present": len(entries) - len(to_fetch), "linked": len(linked),
            "to_fetch": to_fetch}


def parse_result(line: str) -> Optional[dict]:
    if not line.startswith(RESULT_PREFIX):
        return None
    try:
        return json.loads(line[len(RESULT_PREFIX):])
    except ValueError:
        return None


def _reason(error: str) -> tuple:
    """Текст ошибки spotdl → (причина, ссылка YouTube или None, пояснение)."""
    cls = (error or "").split(":", 1)[0].strip()
    reason = REASON_BY_ERROR.get(cls, "other")
    m = _YT_URL.search(error or "")
    return reason, (m.group(0) if m else None), (error or "")[:300]


def apply_results(conn, results: List[dict], now: float) -> dict:
    """Итоги загрузки по трекам → индекс. Вызывать в транзакции.
    Файл, который уже есть в индексе (spotdl нашёл его под другим релизом), —
    «не скачан» сливается с ним; новый файл — трек получает путь (теги
    дочитает сканер); ошибка — причина в missing."""
    st = {"downloaded": 0, "linked": 0, "failed": 0, "by_reason": {}}
    for r in results:
        sp = spotify_id_of({"url": r.get("url") or ""})
        tid = find_track(conn, sp, None) if sp else None
        if tid is None:
            continue
        row = conn.execute("SELECT status FROM tracks WHERE id = ?", (tid,)).fetchone()
        if not row or row[0] != "missing":
            continue
        path = r.get("path")
        if path and os.path.exists(path):
            path = os.path.abspath(path)
            have = conn.execute("SELECT id FROM tracks WHERE path = ?", (path,)).fetchone()
            if have:
                db.merge_tracks(conn, tid, have[0])
                st["linked"] += 1
            else:
                conn.execute("UPDATE tracks SET path = ?, status = 'ok', file_mtime = NULL, updated_at = ? "
                             "WHERE id = ?", (path, now, tid))
                conn.execute("DELETE FROM missing WHERE track_id = ?", (tid,))
                st["downloaded"] += 1
            continue
        reason, yt, detail = _reason(r.get("error") or "")
        yt = yt or r.get("download_url")
        st["failed"] += 1
        st["by_reason"][reason] = st["by_reason"].get(reason, 0) + 1
        conn.execute(
            "INSERT INTO missing(track_id, reason, detail, source_url, tried, attempts, last_attempt_at) "
            "VALUES (?, ?, ?, ?, '[\"youtube\"]', 1, ?) ON CONFLICT(track_id) DO UPDATE SET "
            "reason = excluded.reason, detail = excluded.detail, "
            "source_url = COALESCE(excluded.source_url, missing.source_url), "
            "attempts = missing.attempts + 1, last_attempt_at = excluded.last_attempt_at",
            (tid, reason, detail, yt, now))
    return st


# ------------------------------------------------------------------ добивание
AUTO_FETCH_RETRY = 7 * 86400     # не пробовать тот же источник для трека чаще раза в неделю


def auto_fetch_candidates(conn, playlist_id: int, source: str, now: float) -> List[dict]:
    """Треки плейлиста, которых нет на YouTube, — для автодобивания источником
    source (deezer / auto). Помечает попытку сразу, чтобы следующий синк не
    поставил тот же трек второй раз. Вызывать в транзакции."""
    out = []
    for r in conn.execute(
            "SELECT t.id, t.title, t.artist, m.tried, m.last_attempt_at, "
            "(SELECT value FROM track_ids WHERE track_id = t.id AND kind = 'spotify') AS sp "
            "FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id JOIN missing m ON m.track_id = t.id "
            "WHERE pi.playlist_id = ? AND t.status = 'missing' AND m.reason = 'not_found' "
            "GROUP BY t.id", (playlist_id,)).fetchall():
        if not r["sp"]:
            continue
        try:
            tried = json.loads(r["tried"] or "[]")
        except ValueError:
            tried = []
        if source in tried and r["last_attempt_at"] and now - r["last_attempt_at"] < AUTO_FETCH_RETRY:
            continue
        if source not in tried:
            tried.append(source)
        conn.execute("UPDATE missing SET tried = ?, last_attempt_at = ? WHERE track_id = ?",
                     (json.dumps(tried), now, r["id"]))
        out.append({"id": r["id"], "spotify_url": f"https://open.spotify.com/track/{r['sp']}",
                    "subject": f"{r['artist']} - {r['title']}" if r["artist"] else r["title"]})
    return out


# ------------------------------------------------------------------ синк плейлиста
def _ids(conn, pl) -> tuple:
    """(id плейлиста, id источника Spotify) в индексе."""
    from . import playlists
    pid = playlists.db_id(conn, pl.url)
    if pid is None:
        raise ListError(f"«{pl.name}» нет в индексе")
    sid = conn.execute("SELECT MIN(id) FROM playlist_sources WHERE playlist_id = ? AND kind LIKE 'spotify_%'",
                       (pid,)).fetchone()[0]
    return pid, sid


def _fetch_list(job, pl) -> tuple:
    """Список треков источника → (songs, название на площадке)."""
    from . import jobs
    from .naming import is_saved
    if is_saved(pl.url):
        path, count = jobs._liked_songs_file(job, pl)       # LikedSongsUnavailable — наверх
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), "Liked Songs"
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    out = os.path.join(config.UPLOADS_DIR, f"list-{pl.id}.json")
    job.append(f"[list] получаю список треков: {pl.url}")
    rc, lines = jobs._run_process(job, [sys.executable, "-m", "app.spotify_source", pl.url, out],
                                  cwd=jobs._PROJECT_DIR, timeout=600,
                                  display=["spotify_source", pl.url])
    res = {}
    for ln in reversed(lines):
        if ln.startswith("{"):
            try:
                res = json.loads(ln)
                break
            except ValueError:
                pass
    if not res.get("ok"):
        err = res.get("error") or ("таймаут" if rc and rc < 0 else f"код {rc}")
        if res.get("unavailable"):
            raise ListUnavailable(err)
        raise ListError(f"не удалось получить список: {err}")
    with open(out, "r", encoding="utf-8") as fh:
        songs = json.load(fh)
    try:
        os.remove(out)
    except OSError:
        pass
    return songs, res.get("name") or ""


def _set_source(conn, sid: int, status: str, result: Optional[dict], label: str = "") -> None:
    sets, args = ["status = ?"], [status]
    if result is not None:
        sets += ["last_sync_at = ?", "last_result = ?"]
        args += [time.time(), json.dumps(result, ensure_ascii=False)]
    if label:
        sets.append("label = ?")
        args.append(label)
    conn.execute(f"UPDATE playlist_sources SET {', '.join(sets)} WHERE id = ?", (*args, sid))


def run(job, pl, prog) -> int:
    """Синк одного плейлиста новым способом. Возвращает код spotdl (0 — без
    сбоев; если качать нечего — 0). ListUnavailable / ListError — наверх."""
    from . import jobs, m3u_gen
    prog.set_phase("list")
    try:
        songs, label = _fetch_list(job, pl)
    except ListUnavailable:
        with db.connect() as conn, db.transaction(conn):
            _set_source(conn, _ids(conn, pl)[1], "unavailable", None)
        raise
    if not songs:
        # пустой ответ чаще сбой площадки, чем пустой плейлист: состав и m3u не трогаем
        job.append(f"[sync] «{pl.name}»: площадка вернула пустой список — состав оставлен прежним")
        prog.set_phase("done")
        return 0
    now = time.time()
    with db.connect() as conn:
        with db.transaction(conn):
            pid, sid = _ids(conn, pl)
            plan = reconcile(conn, pid, sid, songs, now)
            conn.execute("UPDATE playlists SET managed = 1 WHERE id = ?", (pid,))
            _set_source(conn, sid, "ok", None, label)
    # save-файл — для прежнего интерфейса и отката на старый образ
    try:
        tmp = config.savefile_path(pl.id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(songs, fh, ensure_ascii=False)
        os.replace(tmp, config.savefile_path(pl.id))
    except OSError:
        pass
    todo = plan["to_fetch"]
    job.append(f"[sync] «{pl.name}»: в списке {plan['tracks']}, уже есть {plan['present']}"
               + (f" (из них найдено под другим релизом {plan['linked']})" if plan["linked"] else "")
               + f", качать {len(todo)}")
    prog.total, prog.base = plan["tracks"], plan["present"]
    prog.processed = prog.skipped = plan["present"]
    prog.set_phase("prepare")

    rc, results = 0, []
    if todo:
        path = os.path.join(config.UPLOADS_DIR, f"fetch-{pl.id}.spotdl")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([song for _tid, song in todo], fh, ensure_ascii=False)

        def on_line(line: str):
            r = parse_result(line)
            if r is not None:
                results.append(r)
                return None
            return prog.feed(line)

        rc, _out = jobs.run_spotdl(job, ["download", path] + jobs._common_output_args() + jobs._youtube_pace_args(),
                                   kill_on_cancel=False, on_line=on_line, results=True)
        try:
            os.remove(path)
        except OSError:
            pass
    with db.connect() as conn:
        with db.transaction(conn):
            st = apply_results(conn, results, time.time())
            result = {"engine": "index", "tracks": plan["tracks"], "present": plan["present"],
                      "linked": plan["linked"] + st["linked"], "downloaded": st["downloaded"],
                      "failed": st["failed"], "by_reason": st["by_reason"], "unreported": len(todo) - len(results),
                      "rc": rc, "at": time.time()}
            _set_source(conn, sid, "ok", result)
        changed = m3u_gen.write_playlist(conn, pid)
    if changed:
        job.append(f"[m3u] плейлист обновлён: {os.path.basename(m3u_gen.path_for(pl.name))}")
    # «ненайденные» прежнего интерфейса для этого плейлиста больше не ведутся
    try:
        os.remove(config.errors_path(pl.safe))
    except OSError:
        pass
    if todo and len(results) < len(todo):
        job.append(f"[sync] по {len(todo) - len(results)} трекам spotdl не сообщил итог — "
                   "они остаются «не скачанными» до следующего синка")
    return rc
