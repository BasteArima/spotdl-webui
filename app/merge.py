"""Склейка дублей (design/plan.md, задача 4.4).

Решение «один трек» (пользователя во «Внимании» или авто-склейка уверенных
пар) применяется так:

  • остаётся файл лучшего качества: без потерь → выше битрейт → тот, что уже
    в большем числе плейлистов → старший;
  • второй файл НЕ удаляется, а переезжает в корзину сервиса —
    /music/.trash/<тот же относительный путь> (с ним и .lrc рядом). В корзине
    лежит .ndignore, Navidrome её не индексирует; сканер индекса тоже;
  • ряд второго трека остаётся в индексе со статусом trashed (откуда убран,
    с чем склеен) — по нему корзину можно вернуть;
  • плейлисты и внешние ID переходят на оставшийся трек; m3u переписываются:
    те, что собирает сервис, — заново из индекса, прежние (spotdl) — заменой
    строки пути.

Авто-склейка берёт только пары уровней 1–3, где совпадение подтверждается и
метаданными, а не одним общим ID: spotdl бывает берёт один ролик YouTube для
двух разных песен — такие пары остаются решать пользователю.

Запуск: python -m app.merge [--auto] — применить решения «один трек» (и с
--auto — уверенные пары); итог — JSON последней строкой. Под library_lock:
скан индекса в это время ждёт.
"""
import json
import os
import shutil
import sys
import time
from typing import Dict, List, Optional

from . import config, db, matching

TRASH_DIR = ".trash"
LOSSLESS = {"flac", "alac", "wav", "aiff", "ape", "wv"}


def trash_root() -> str:
    return os.path.join(config.MUSIC_DIR, TRASH_DIR)


def _ensure_trash() -> None:
    root = trash_root()
    os.makedirs(root, exist_ok=True)
    marker = os.path.join(root, ".ndignore")      # Navidrome: папку не сканировать
    if not os.path.exists(marker):
        open(marker, "w").close()


def _trash_path(path: str) -> str:
    rel = os.path.relpath(path, config.MUSIC_DIR)
    if rel.startswith(".."):
        rel = os.path.basename(path)
    dst = os.path.join(trash_root(), rel)
    base, ext = os.path.splitext(dst)
    n = 2
    while os.path.exists(dst):
        dst = f"{base} ({n}){ext}"
        n += 1
    return dst


def _move(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)          # тот же диск — просто переименование
    lrc_src, lrc_dst = os.path.splitext(src)[0] + ".lrc", os.path.splitext(dst)[0] + ".lrc"
    if os.path.exists(lrc_src) and not os.path.exists(lrc_dst):
        shutil.move(lrc_src, lrc_dst)


def _rank(conn, t) -> tuple:
    plists = conn.execute("SELECT COUNT(DISTINCT playlist_id) FROM playlist_items WHERE track_id = ?",
                          (t["id"],)).fetchone()[0]
    return ((t["codec"] or "").lower() in LOSSLESS, t["bitrate"] or 0, plists, -t["id"])


def resolve(conn, track_id: int) -> Optional[int]:
    """Трек, в который в итоге склеен track_id (цепочки A→B→C)."""
    seen = set()
    while track_id and track_id not in seen:
        seen.add(track_id)
        row = conn.execute("SELECT status, merged_into FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            return None
        if row["status"] != "trashed" or not row["merged_into"]:
            return track_id
        track_id = row["merged_into"]
    return None


def merge_pair(conn, a_id: int, b_id: int, now: float) -> Optional[dict]:
    """Склеить два трека: худший файл — в корзину. Вызывать в транзакции; файл
    переносится до записи в базу и возвращается на место, если запись не
    удалась (исключение наверх). None — склеивать нечего (уже один трек, нет
    файла)."""
    a_id, b_id = resolve(conn, a_id), resolve(conn, b_id)
    if not a_id or not b_id or a_id == b_id:
        return None
    a, b = (conn.execute("SELECT * FROM tracks WHERE id = ?", (i,)).fetchone() for i in (a_id, b_id))
    if a["status"] != "ok" or b["status"] != "ok" or not a["path"] or not b["path"]:
        return None
    win, lose = (a, b) if _rank(conn, a) >= _rank(conn, b) else (b, a)
    if not os.path.exists(lose["path"]):
        return None                  # файла уже нет — сканер разберётся
    _ensure_trash()
    dst = _trash_path(lose["path"])
    _move(lose["path"], dst)
    try:
        conn.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, source_id, track_id, position, added_at) "
                     "SELECT playlist_id, source_id, ?, position, added_at FROM playlist_items WHERE track_id = ?",
                     (win["id"], lose["id"]))
        conn.execute("DELETE FROM playlist_items WHERE track_id = ?", (lose["id"],))
        conn.execute("UPDATE track_ids SET track_id = ? WHERE track_id = ?", (win["id"], lose["id"]))
        conn.execute("UPDATE tracks SET status = 'trashed', path = ?, trashed_from = ?, merged_into = ?, "
                     "trashed_at = ?, updated_at = ? WHERE id = ?", (dst, lose["path"], win["id"], now, now, lose["id"]))
        # нерешённые пары с убранным треком больше не нужны (дедуп найдёт их у оставшегося)
        conn.execute("DELETE FROM match_candidates WHERE decision IS NULL AND (track_a = ? OR track_b = ?)",
                     (lose["id"], lose["id"]))
    except BaseException:
        _move(dst, lose["path"])
        raise
    return {"winner": win["path"], "loser": lose["path"], "trash": dst,
            "title": f"{lose['artist']} — {lose['title']}" if lose["artist"] else lose["title"]}


def _side(r) -> dict:
    try:
        artists = json.loads(r["artists"] or "[]")
    except ValueError:
        artists = []
    return {"title": r["title"], "artists": artists, "duration": r["duration"], "isrc": r["isrc"], "ids": {}}


def auto_eligible(conn) -> List[int]:
    """Нерешённые пары уровней 1–3, где и метаданные говорят «та же запись»
    (сравнение без общих ID). id кандидатов."""
    out = []
    for c in conn.execute("SELECT c.id, a.title AS ta, a.artists AS aa, a.duration AS da, a.isrc AS ia, "
                          "b.title AS tb, b.artists AS ab, b.duration AS db, b.isrc AS ib "
                          "FROM match_candidates c JOIN tracks a ON a.id = c.track_a JOIN tracks b ON b.id = c.track_b "
                          "WHERE c.decision IS NULL AND c.level <= 3 AND a.status = 'ok' AND b.status = 'ok'").fetchall():
        res = matching.compare(_side({"title": c["ta"], "artists": c["aa"], "duration": c["da"], "isrc": c["ia"]}),
                               _side({"title": c["tb"], "artists": c["ab"], "duration": c["db"], "isrc": c["ib"]}))
        if res and res[0] <= 3:
            out.append(c["id"])
    return out


def fix_m3u(replaced: Dict[str, str]) -> int:
    """Прежние m3u (их пишет spotdl): путь убранного файла → путь оставшегося;
    повтор того же файла в плейлисте убирается вместе со строкой #EXTINF."""
    if not replaced or not os.path.isdir(config.PLAYLISTS_M3U_DIR):
        return 0
    changed = 0
    for name in os.listdir(config.PLAYLISTS_M3U_DIR):
        if not name.lower().endswith((".m3u", ".m3u8")):
            continue
        path = os.path.join(config.PLAYLISTS_M3U_DIR, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        if not any(ln in replaced for ln in lines):
            continue
        out, seen, pending = [], set(), None
        for ln in lines:
            if ln.startswith("#EXTINF"):
                pending = ln
                continue
            if ln.startswith("#") or not ln.strip():
                out.append(ln)
                continue
            ln = replaced.get(ln, ln)
            if ln in seen:
                pending = None
                continue
            seen.add(ln)
            if pending:
                out.append(pending)
            out.append(ln)
            pending = None
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
        changed += 1
    return changed


def apply(db_path: Optional[str] = None, auto: bool = False, log=print) -> dict:
    """Применить решения «один трек» (и уверенные пары при auto). Возвращает итог."""
    from . import m3u_gen
    rep = {"merged": 0, "skipped": 0, "errors": 0, "auto": 0, "m3u_fixed": 0, "m3u_written": 0}
    replaced: Dict[str, str] = {}
    with db.connect(db_path) as conn:
        if auto:
            ids = auto_eligible(conn)
            if ids:
                with db.transaction(conn):
                    conn.executemany("UPDATE match_candidates SET decision = 'same', decided_at = ? WHERE id = ?",
                                     [(time.time(), i) for i in ids])
                rep["auto"] = len(ids)
        todo = conn.execute("SELECT id, track_a, track_b FROM match_candidates WHERE decision = 'same' "
                            "AND applied_at IS NULL ORDER BY level, id").fetchall()
        for c in todo:
            now = time.time()
            try:
                with db.transaction(conn):
                    res = merge_pair(conn, c["track_a"], c["track_b"], now)
                    conn.execute("UPDATE match_candidates SET applied_at = ? WHERE id = ?", (now, c["id"]))
            except Exception as e:  # noqa: BLE001 — одна пара не должна останавливать остальные
                rep["errors"] += 1
                log(f"[merge] не удалось склеить пару {c['id']}: {type(e).__name__}: {e}")
                continue
            if not res:
                rep["skipped"] += 1
                continue
            rep["merged"] += 1
            replaced[res["loser"]] = res["winner"]
            log(f"[merge] {res['title']}: оставлен {os.path.relpath(res['winner'], config.MUSIC_DIR)}, "
                f"в корзину {os.path.relpath(res['loser'], config.MUSIC_DIR)}")
        rep["m3u_fixed"] = fix_m3u(replaced)
        if replaced:
            rep["m3u_written"] = m3u_gen.write_managed(conn)
    return rep


# ------------------------------------------------------------------ корзина
def trash_list(conn) -> List[dict]:
    out = []
    for r in conn.execute("SELECT t.*, w.title AS w_title, w.artist AS w_artist, w.path AS w_path "
                          "FROM tracks t LEFT JOIN tracks w ON w.id = t.merged_into "
                          "WHERE t.status = 'trashed' ORDER BY t.trashed_at DESC, t.id DESC"):
        out.append({"id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"],
                    "bitrate": r["bitrate"], "codec": r["codec"], "size": r["file_size"],
                    "from": r["trashed_from"], "at": r["trashed_at"],
                    "exists": bool(r["path"] and os.path.exists(r["path"])),
                    "kept": {"title": r["w_title"], "artist": r["w_artist"], "path": r["w_path"]}})
    return out


def restore(conn, track_id: int) -> str:
    """Вернуть файл из корзины на прежнее место; пара помечается «разные треки».
    Плейлисты остаются у оставшегося трека. Вызывать в транзакции."""
    r = conn.execute("SELECT * FROM tracks WHERE id = ? AND status = 'trashed'", (track_id,)).fetchone()
    if not r:
        raise ValueError("В корзине такого трека нет")
    if not r["path"] or not os.path.exists(r["path"]):
        raise ValueError("Файла в корзине уже нет")
    dst = r["trashed_from"]
    if not dst or os.path.exists(dst):
        raise ValueError("На прежнем месте уже лежит файл — вернуть некуда")
    now = time.time()
    _move(r["path"], dst)
    conn.execute("UPDATE tracks SET status = 'ok', path = ?, merged_into = NULL, trashed_from = NULL, "
                 "trashed_at = NULL, file_mtime = NULL, updated_at = ? WHERE id = ?", (dst, now, track_id))
    if r["merged_into"]:
        a, b = sorted((track_id, r["merged_into"]))
        conn.execute("INSERT INTO match_candidates(track_a, track_b, score, level, reason, decision, decided_at, "
                     "created_at) VALUES (?, ?, 0, 4, 'возвращён из корзины', 'different', ?, ?) "
                     "ON CONFLICT(track_a, track_b) DO UPDATE SET decision = 'different', decided_at = excluded.decided_at",
                     (a, b, now, now))
    return dst


def empty_trash(conn) -> dict:
    """Удалить файлы корзины насовсем (только по явной команде пользователя)."""
    rows = conn.execute("SELECT id, path FROM tracks WHERE status = 'trashed'").fetchall()
    freed, files = 0, 0
    for r in rows:
        p = r["path"]
        if p and os.path.exists(p) and os.path.abspath(p).startswith(os.path.abspath(trash_root()) + os.sep):
            freed += os.path.getsize(p)
            os.remove(p)
            files += 1
            lrc = os.path.splitext(p)[0] + ".lrc"
            if os.path.exists(lrc):
                os.remove(lrc)
    conn.execute("DELETE FROM tracks WHERE status = 'trashed'")
    # пустые папки корзины
    for d, _subs, _files in sorted(os.walk(trash_root()), key=lambda x: -len(x[0])):
        if d != trash_root():
            try:
                os.rmdir(d)
            except OSError:
                pass
    return {"files": files, "bytes": freed}


def _cli() -> None:
    auto = "--auto" in sys.argv[1:]
    try:
        with db.library_lock():
            rep = apply(auto=auto, log=lambda m: print(m, flush=True))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}), flush=True)
        sys.exit(1)
    print(f"[merge] склеено {rep['merged']}" + (f" (из них уверенных автоматически {rep['auto']})" if rep["auto"] else "")
          + (f", пропущено {rep['skipped']}" if rep["skipped"] else "")
          + (f", ошибок {rep['errors']}" if rep["errors"] else "")
          + f" · m3u: переписано {rep['m3u_written']}, исправлено прежних {rep['m3u_fixed']}", flush=True)
    print(json.dumps(dict(rep, ok=True)), flush=True)


if __name__ == "__main__":
    _cli()
