"""Поиск одинаковых записей по всей библиотеке (design/plan.md, задачи 1.4–1.5).

Файлы НЕ трогаются. Проход делает две вещи:
  • пары скачанных файлов одной записи → match_candidates (уровень, процент,
    причина); решение «склеить / разные» — за пользователем или авто-склейкой
    (этап 4);
  • трек «не скачан», для которого в библиотеке уже есть файл той же записи
    (уровни 1–3), — связывается с этим файлом: плейлисты переходят на файл. Так
    закрываются «прочие» недокачанные, которые spotdl сопоставил с файлом
    другого релиза. Уровень 4 — только кандидат, без связывания.

Кандидаты ищутся не перебором всех пар (тысячи файлов), а внутри групп:
одинаковый ISRC, одинаковый ключ названия (norm_key), общий исполнитель.
"""
import json
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional

from . import db, matching

_GROUP_CAP = 400          # группа больше — пары не перебираем (это не один трек, а «Various Artists»)


def refresh_norm_keys(conn) -> int:
    """norm_key у треков без него; поменялись правила (NORM_VERSION) — у всех."""
    full = db.get_meta(conn, "norm_version") != str(matching.NORM_VERSION)
    rows = conn.execute("SELECT id, title FROM tracks" + ("" if full else " WHERE norm_key IS NULL")).fetchall()
    for r in rows:
        conn.execute("UPDATE tracks SET norm_key = ? WHERE id = ?", (matching.title_key(r["title"]), r["id"]))
    db.set_meta(conn, "norm_version", str(matching.NORM_VERSION))
    return len(rows)


def _load(conn) -> Dict[int, dict]:
    tracks: Dict[int, dict] = {}
    for r in conn.execute("SELECT id, status, title, artists, duration, isrc, norm_key, bitrate, path "
                          "FROM tracks WHERE status IN ('ok', 'missing')"):
        try:
            artists = json.loads(r["artists"] or "[]")
        except ValueError:
            artists = []
        tracks[r["id"]] = {"id": r["id"], "status": r["status"], "title": r["title"], "artists": artists,
                           "duration": r["duration"], "isrc": r["isrc"], "norm_key": r["norm_key"],
                           "bitrate": r["bitrate"], "path": r["path"], "ids": {}}
    for r in conn.execute("SELECT kind, value, track_id FROM track_ids"):
        t = tracks.get(r["track_id"])
        if t is not None:
            t["ids"][r["kind"]] = r["value"]
    return tracks


def _group_keys(t: dict) -> list:
    """Группы, внутри которых ищутся пары: ISRC, ключ названия, исполнители."""
    keys = [("isrc", t["isrc"])] if t["isrc"] else []
    if t["norm_key"]:
        keys.append(("title", t["norm_key"]))
    keys += [("artist", k) for k in matching.artist_keys(t["artists"])]
    return keys


def _pairs(tracks: Dict[int, dict]):
    """Пары-претенденты: внутри групп по ISRC, ключу названия, исполнителю."""
    groups = defaultdict(list)
    for t in tracks.values():
        for key in _group_keys(t):
            groups[key].append(t["id"])
    seen = set()
    for ids in groups.values():
        if len(ids) < 2 or len(ids) > _GROUP_CAP:
            continue
        for a, b in combinations(sorted(set(ids)), 2):
            if (a, b) not in seen:
                seen.add((a, b))
                yield a, b


def find_duplicates(db_path: Optional[str] = None) -> dict:
    """Проход по библиотеке; возвращает отчёт (и пишет его в meta)."""
    report = {"norm_keys_updated": 0, "pairs_checked": 0, "candidates": {1: 0, 2: 0, 3: 0, 4: 0},
              "candidates_new": 0, "missing_linked": 0, "missing_maybe": 0, "examples": []}
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            report["norm_keys_updated"] = refresh_norm_keys(conn)
            tracks = _load(conn)
            now = __import__("time").time()
            link: Dict[int, tuple] = {}            # «не скачан» → (лучший файл, уровень)
            for a, b in _pairs(tracks):
                ta, tb = tracks[a], tracks[b]
                if ta["status"] == "missing" and tb["status"] == "missing":
                    continue
                report["pairs_checked"] += 1
                res = matching.compare(ta, tb)
                if not res:
                    continue
                level, score, reason = res
                if ta["status"] != tb["status"]:
                    # одна сторона ждёт как «не скачан», у другой есть файл
                    miss, have = (ta, tb) if ta["status"] == "missing" else (tb, ta)
                    if level <= 3:
                        best = link.get(miss["id"])
                        if not best or level < best[1]:
                            link[miss["id"]] = (have["id"], level)
                        continue
                    report["missing_maybe"] += 1
                cur = conn.execute("INSERT OR IGNORE INTO match_candidates(track_a, track_b, score, level, reason, "
                                   "created_at) VALUES (?, ?, ?, ?, ?, ?)", (a, b, score, level, reason, now))
                if cur.rowcount:
                    report["candidates_new"] += 1
                else:   # пересчитали — обновим оценку, если ещё не решено и не была точнее
                    # (уровень 1 «общий ID» ставит сканер: у второго файла ID в track_ids
                    # не попадает, и compare его уже не видит)
                    conn.execute("UPDATE match_candidates SET score = ?, level = ?, reason = ? "
                                 "WHERE track_a = ? AND track_b = ? AND decision IS NULL AND level >= ?",
                                 (score, level, reason, a, b, level))
            for miss_id, (have_id, _lvl) in link.items():
                db.merge_tracks(conn, miss_id, have_id)
            report["missing_linked"] = len(link)
            for lvl, n in conn.execute("SELECT level, COUNT(*) FROM match_candidates WHERE decision IS NULL "
                                       "GROUP BY level"):
                report["candidates"][lvl] = n
            for r in conn.execute(
                    "SELECT m.level, m.reason, a.title AS ta, a.artist AS aa, b.title AS tb, b.artist AS ab "
                    "FROM match_candidates m JOIN tracks a ON a.id = m.track_a JOIN tracks b ON b.id = m.track_b "
                    "WHERE m.decision IS NULL ORDER BY m.level, m.score DESC LIMIT 15"):
                report["examples"].append(f"[{r['level']}] {r['aa']} — {r['ta']}  ↔  {r['ab']} — {r['tb']} · {r['reason']}")
            db.set_meta(conn, "dedupe", json.dumps({k: v for k, v in report.items() if k != "examples"}))
    return report


def link_missing(conn, ids) -> Dict[int, int]:
    """«Не скачанные» ids, для которых в библиотеке уже есть файл той же записи
    (уровни 1–3), — связать с файлом (db.merge_tracks), чтобы синк не качал
    второй раз то, что уже лежит под другим релизом. Вызывать в транзакции.
    Возвращает {id «не скачанного»: id файла}."""
    ids = set(ids)
    if not ids:
        return {}
    refresh_norm_keys(conn)
    tracks = _load(conn)
    groups = defaultdict(list)
    for t in tracks.values():
        if t["status"] == "ok":
            for key in _group_keys(t):
                groups[key].append(t["id"])
    linked: Dict[int, int] = {}
    for mid in ids:
        miss = tracks.get(mid)
        if not miss or miss["status"] != "missing":
            continue
        best = None
        cands = set()
        for key in _group_keys(miss):
            if len(groups.get(key, ())) <= _GROUP_CAP:
                cands.update(groups.get(key, ()))
        for fid in cands:
            res = matching.compare(miss, tracks[fid])
            if res and res[0] <= 3 and (best is None or res[0] < best[1]):
                best = (fid, res[0])
        if best:
            db.merge_tracks(conn, mid, best[0])
            linked[mid] = best[0]
    return linked
