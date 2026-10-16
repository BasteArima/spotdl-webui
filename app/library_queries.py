"""Выборки из индекса библиотеки для нового интерфейса (app/static/v2).

Только чтение. Все функции берут открытое соединение (app/db.connect) и
возвращают простые словари — их отдают /api/v2/* как есть.
"""
import json
from typing import Dict, List, Optional

# вид источника плейлиста → площадка (для значков)
PLATFORM = {"spotify_playlist": "spotify", "spotify_album": "spotify", "spotify_artist": "spotify",
            "spotify_track": "spotify", "spotify_liked": "spotify", "ytm_playlist": "ytm", "ytm_liked": "ytm"}


def track_platforms(conn, track_ids: List[int]) -> Dict[int, List[str]]:
    """Из каких площадок трек попал в библиотеку (по плейлистам-источникам).
    Ни в одном плейлисте — «manual» (загружен вручную / без источника)."""
    out: Dict[int, set] = {t: set() for t in track_ids}
    if not track_ids:
        return {}
    marks = ",".join("?" for _ in track_ids)
    for r in conn.execute(
            f"SELECT pi.track_id, ps.kind FROM playlist_items pi JOIN playlist_sources ps ON ps.id = pi.source_id "
            f"WHERE pi.track_id IN ({marks})", track_ids):
        out[r["track_id"]].add(PLATFORM.get(r["kind"], "manual"))
    order = ["spotify", "ytm", "manual"]
    return {t: sorted(s or {"manual"}, key=order.index) for t, s in out.items()}


def _track_row(r, platforms: Dict[int, List[str]]) -> dict:
    try:
        artists = json.loads(r["artists"] or "[]")
    except ValueError:
        artists = []
    return {"id": r["id"], "title": r["title"], "artist": r["artist"], "artists": artists, "album": r["album"],
            "duration": r["duration"], "codec": r["codec"], "bitrate": r["bitrate"], "status": r["status"],
            "added": r["file_mtime"] or r["added_at"], "platforms": platforms.get(r["id"], ["manual"])}


def home(conn) -> dict:
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731

    files = one("SELECT COUNT(*) FROM tracks WHERE status = 'ok'")
    size = one("SELECT COALESCE(SUM(file_size), 0) FROM tracks WHERE status = 'ok'")

    # ---- источники
    kinds = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM playlist_sources GROUP BY kind")}
    def liked(kind):
        if not kinds.get(kind):
            return None
        row = conn.execute(
            "SELECT COUNT(DISTINCT pi.track_id) AS total, "
            "COUNT(DISTINCT CASE WHEN t.status = 'ok' THEN pi.track_id END) AS have "
            "FROM playlist_items pi JOIN playlist_sources ps ON ps.id = pi.source_id "
            "JOIN tracks t ON t.id = pi.track_id WHERE ps.kind = ?", (kind,)).fetchone()
        return {"total": row["total"], "have": row["have"]}
    spotify_lists = sum(n for k, n in kinds.items() if k.startswith("spotify_") and k != "spotify_liked")
    ytm_lists = sum(n for k, n in kinds.items() if k.startswith("ytm_") and k != "ytm_liked")
    manual = one("SELECT COUNT(*) FROM tracks WHERE status = 'ok' AND id NOT IN (SELECT track_id FROM playlist_items)")
    sources = {
        "spotify": {"playlists": spotify_lists, "liked": liked("spotify_liked")},
        "ytm": {"playlists": ytm_lists, "liked": liked("ytm_liked")},
        "manual": {"tracks": manual},
    }

    # ---- что требует решения
    reasons = {r["reason"]: r["n"] for r in conn.execute(
        "SELECT m.reason, COUNT(*) AS n FROM missing m JOIN tracks t ON t.id = m.track_id "
        "WHERE t.status = 'missing' GROUP BY m.reason")}
    missing_total = one("SELECT COUNT(*) FROM tracks WHERE status = 'missing'")
    dupes = {r["level"]: r["n"] for r in conn.execute(
        "SELECT level, COUNT(*) AS n FROM match_candidates WHERE decision IS NULL GROUP BY level")}
    unavailable = [dict(r) for r in conn.execute(
        "SELECT p.id, p.name, ps.kind, ps.ref FROM playlist_sources ps JOIN playlists p ON p.id = ps.playlist_id "
        "WHERE ps.status = 'unavailable' ORDER BY p.position")]

    # ---- недавно добавленное (по времени файла: первый скан ставит added_at всем сразу)
    rows = conn.execute("SELECT * FROM tracks WHERE status = 'ok' ORDER BY COALESCE(file_mtime, added_at) DESC, id DESC "
                        "LIMIT 6").fetchall()
    plats = track_platforms(conn, [r["id"] for r in rows])

    return {
        "library": {"files": files, "size": size, "playlists": one("SELECT COUNT(*) FROM playlists")},
        "sources": sources,
        "attention": {"missing": missing_total, "missing_by_reason": reasons,
                      "dupes": sum(dupes.values()), "dupes_by_level": dupes, "unavailable": unavailable},
        "recent": [_track_row(r, plats) for r in rows],
    }


# ------------------------------------------------------------------ плейлисты
_SOURCE_LABEL = {"spotify_playlist": "плейлист Spotify", "spotify_album": "альбом Spotify",
                 "spotify_artist": "исполнитель Spotify", "spotify_track": "трек Spotify",
                 "spotify_liked": "Любимые Spotify", "ytm_playlist": "плейлист YouTube Music",
                 "ytm_liked": "Понравившиеся YouTube Music"}


def _sources(conn, playlist_ids: List[int]) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {p: [] for p in playlist_ids}
    if not playlist_ids:
        return out
    marks = ",".join("?" for _ in playlist_ids)
    for r in conn.execute(f"SELECT * FROM playlist_sources WHERE playlist_id IN ({marks}) ORDER BY id", playlist_ids):
        try:
            last = json.loads(r["last_result"]) if r["last_result"] else None
        except ValueError:
            last = None
        out[r["playlist_id"]].append({
            "id": r["id"], "kind": r["kind"], "ref": r["ref"], "label": r["label"] or _SOURCE_LABEL.get(r["kind"], r["kind"]),
            "platform": PLATFORM.get(r["kind"], "manual"), "status": r["status"], "last": last})
    return out


def _counts(conn, playlist_ids: List[int]) -> Dict[int, dict]:
    out = {p: {"total": 0, "have": 0, "missing": 0, "multi": 0} for p in playlist_ids}
    if not playlist_ids:
        return out
    marks = ",".join("?" for _ in playlist_ids)
    for r in conn.execute(
            f"SELECT pi.playlist_id AS pid, COUNT(DISTINCT pi.track_id) AS total, "
            f"COUNT(DISTINCT CASE WHEN t.status = 'ok' THEN pi.track_id END) AS have "
            f"FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id WHERE pi.playlist_id IN ({marks}) "
            f"GROUP BY pi.playlist_id", playlist_ids):
        out[r["pid"]].update(total=r["total"], have=r["have"], missing=r["total"] - r["have"])
    # треки, пришедшие сразу из нескольких источников плейлиста (один файл на всех)
    for r in conn.execute(
            f"SELECT playlist_id AS pid, COUNT(*) AS n FROM (SELECT playlist_id, track_id FROM playlist_items "
            f"WHERE playlist_id IN ({marks}) GROUP BY playlist_id, track_id HAVING COUNT(DISTINCT source_id) > 1) "
            f"GROUP BY playlist_id", playlist_ids):
        out[r["pid"]]["multi"] = r["n"]
    return out


def playlists(conn) -> List[dict]:
    rows = conn.execute("SELECT * FROM playlists ORDER BY position, id").fetchall()
    ids = [r["id"] for r in rows]
    srcs, counts = _sources(conn, ids), _counts(conn, ids)
    return [{"id": r["id"], "name": r["name"], "legacy_id": r["legacy_id"], "auto_sync": bool(r["auto_sync"]),
             "sources": srcs[r["id"]], **counts[r["id"]]} for r in rows]


def playlist(conn, playlist_id: int) -> Optional[dict]:
    r = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    if not r:
        return None
    srcs, counts = _sources(conn, [playlist_id]), _counts(conn, [playlist_id])
    source_kind = {s["id"]: s["kind"] for s in srcs[playlist_id]}
    rows = conn.execute(
        "SELECT t.*, MIN(pi.position) AS pos, GROUP_CONCAT(DISTINCT pi.source_id) AS src_ids, "
        "m.reason AS m_reason, m.detail AS m_detail, m.source_url AS m_url, "
        "(SELECT value FROM track_ids WHERE track_id = t.id AND kind = 'spotify') AS spotify_id "
        "FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id LEFT JOIN missing m ON m.track_id = t.id "
        "WHERE pi.playlist_id = ? GROUP BY t.id ORDER BY pos, t.id", (playlist_id,)).fetchall()
    tracks = []
    for t in rows:
        sids = [int(x) for x in (t["src_ids"] or "").split(",") if x]
        plats = sorted({PLATFORM.get(source_kind.get(s, ""), "manual") for s in sids} or {"manual"},
                       key=["spotify", "ytm", "manual"].index)
        d = _track_row(t, {t["id"]: plats})
        d["spotify_url"] = f"https://open.spotify.com/track/{t['spotify_id']}" if t["spotify_id"] else None
        d["isrc"] = t["isrc"]
        if t["status"] == "missing":
            d["missing"] = {"reason": t["m_reason"] or "other", "detail": t["m_detail"] or "",
                            "source_url": t["m_url"]}
        tracks.append(d)
    return {"id": r["id"], "name": r["name"], "legacy_id": r["legacy_id"], "auto_sync": bool(r["auto_sync"]),
            "sources": srcs[playlist_id], **counts[playlist_id], "tracks": tracks}


def track_for_fetch(conn, track_id: int, playlist_id: Optional[int] = None) -> Optional[dict]:
    """Что нужно загрузчикам: ссылка Spotify, подпись, имя плейлиста (его m3u и
    errors-файл), найденная ссылка YouTube."""
    t = conn.execute("SELECT t.*, (SELECT value FROM track_ids WHERE track_id = t.id AND kind = 'spotify') AS sp, "
                     "m.source_url AS yt FROM tracks t LEFT JOIN missing m ON m.track_id = t.id WHERE t.id = ?",
                     (track_id,)).fetchone()
    if not t:
        return None
    if playlist_id:
        pl = conn.execute("SELECT name FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    else:
        pl = conn.execute("SELECT p.name FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id "
                          "WHERE pi.track_id = ? ORDER BY p.position LIMIT 1", (track_id,)).fetchone()
    return {"id": t["id"], "status": t["status"], "isrc": t["isrc"],
            "spotify_url": f"https://open.spotify.com/track/{t['sp']}" if t["sp"] else None,
            "subject": f"{t['artist']} - {t['title']}" if t["artist"] else t["title"],
            "playlist": pl["name"] if pl else "", "youtube_url": t["yt"]}


# ------------------------------------------------------------------ «Внимание»
def _side(conn, t, plats: Dict[int, List[str]]) -> dict:
    d = _track_row(t, plats)
    d.update(path=t["path"], isrc=t["isrc"], album_artist=t["album_artist"])
    d["playlists"] = [r[0] for r in conn.execute(
        "SELECT DISTINCT p.name FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id "
        "WHERE pi.track_id = ? ORDER BY p.position", (t["id"],))]
    return d


def attention(conn) -> dict:
    """Всё, что требует решения: недокачанные (с плейлистом и причиной),
    возможные дубли без решения, недоступные источники."""
    rows = conn.execute(
        "SELECT t.*, m.reason AS m_reason, m.detail AS m_detail, m.source_url AS m_url, "
        "(SELECT value FROM track_ids WHERE track_id = t.id AND kind = 'spotify') AS spotify_id, "
        "(SELECT p.name FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id "
        " WHERE pi.track_id = t.id ORDER BY p.position LIMIT 1) AS playlist, "
        "(SELECT p.id FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id "
        " WHERE pi.track_id = t.id ORDER BY p.position LIMIT 1) AS playlist_id "
        "FROM tracks t LEFT JOIN missing m ON m.track_id = t.id WHERE t.status = 'missing' "
        "ORDER BY playlist, t.title").fetchall()
    missing = []
    for t in rows:
        d = _track_row(t, {})
        d.update(spotify_url=f"https://open.spotify.com/track/{t['spotify_id']}" if t["spotify_id"] else None,
                 isrc=t["isrc"], playlist=t["playlist"], playlist_id=t["playlist_id"],
                 missing={"reason": t["m_reason"] or "other", "detail": t["m_detail"] or "", "source_url": t["m_url"]})
        d.pop("platforms", None)
        missing.append(d)
    # все плейлисты каждого недокачанного трека (фильтр «Внимания» по плейлисту)
    in_pl: Dict[int, List[dict]] = {}
    for r in conn.execute("SELECT DISTINCT pi.track_id, p.id, p.name, p.position FROM playlist_items pi "
                          "JOIN playlists p ON p.id = pi.playlist_id JOIN tracks t ON t.id = pi.track_id "
                          "WHERE t.status = 'missing' ORDER BY p.position, p.id"):
        in_pl.setdefault(r["track_id"], []).append({"id": r["id"], "name": r["name"]})
    for d in missing:
        d["playlists"] = in_pl.get(d["id"], [])

    cands = conn.execute(
        "SELECT * FROM match_candidates WHERE decision IS NULL ORDER BY level, score DESC, id").fetchall()
    tids = sorted({c["track_a"] for c in cands} | {c["track_b"] for c in cands})
    plats = track_platforms(conn, tids)
    by_id = {t["id"]: t for t in conn.execute(
        f"SELECT * FROM tracks WHERE id IN ({','.join('?' for _ in tids) or 'NULL'})", tids)}
    from . import merge
    safe = set(merge.auto_eligible(conn))   # уверенные, подтверждённые и названием
    dupes = []
    for c in cands:
        a, b = by_id.get(c["track_a"]), by_id.get(c["track_b"])
        if not a or not b:
            continue
        dupes.append({"id": c["id"], "level": c["level"], "score": c["score"], "reason": c["reason"],
                      "safe": c["id"] in safe, "a": _side(conn, a, plats), "b": _side(conn, b, plats)})
    decided_same = conn.execute("SELECT COUNT(*) FROM match_candidates WHERE decision = 'same' "
                                "AND applied_at IS NULL").fetchone()[0]
    trashed = conn.execute("SELECT COUNT(*) FROM tracks WHERE status = 'trashed'").fetchone()[0]

    unavailable = [dict(r) for r in conn.execute(
        "SELECT p.id, p.name, ps.kind, ps.ref FROM playlist_sources ps JOIN playlists p ON p.id = ps.playlist_id "
        "WHERE ps.status = 'unavailable' ORDER BY p.position")]
    return {"missing": missing, "dupes": dupes, "dupes_pending_merge": decided_same, "trashed": trashed,
            "unavailable": unavailable}


# ------------------------------------------------------------------ библиотека
def _ulower(conn) -> None:
    # LIKE и lower() в SQLite понимают регистр только латиницы — для кириллицы своя функция
    conn.create_function("ulower", 1, lambda s: (s or "").lower(), deterministic=True)


_PLATFORM_SQL = {
    "spotify": "EXISTS (SELECT 1 FROM playlist_items pi JOIN playlist_sources ps ON ps.id = pi.source_id "
               "WHERE pi.track_id = t.id AND ps.kind LIKE 'spotify_%')",
    "ytm": "EXISTS (SELECT 1 FROM playlist_items pi JOIN playlist_sources ps ON ps.id = pi.source_id "
           "WHERE pi.track_id = t.id AND ps.kind LIKE 'ytm_%')",
    "manual": "NOT EXISTS (SELECT 1 FROM playlist_items pi WHERE pi.track_id = t.id)",
}


def library_overview(conn, min_bitrate: int, upgraded_urls: set) -> dict:
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    # трек, а не ID: у склеенного трека ID Spotify бывает несколько
    sp_ok = conn.execute(
        "SELECT t.bitrate, GROUP_CONCAT(i.value) AS ids FROM tracks t JOIN track_ids i ON i.track_id = t.id "
        "AND i.kind = 'spotify' WHERE t.status = 'ok' GROUP BY t.id").fetchall()
    below = [r for r in sp_ok if (r["bitrate"] or 0) < min_bitrate]
    pending = [r for r in below if not any(f"https://open.spotify.com/track/{v}" in upgraded_urls
                                           for v in (r["ids"] or "").split(",") if v)]
    return {
        "tracks": one("SELECT COUNT(*) FROM tracks WHERE status = 'ok'"),
        "size": one("SELECT COALESCE(SUM(file_size), 0) FROM tracks WHERE status = 'ok'"),
        "artists": one("SELECT COUNT(DISTINCT artist) FROM tracks WHERE status = 'ok' AND artist != ''"),
        "albums": one("SELECT COUNT(*) FROM (SELECT 1 FROM tracks WHERE status = 'ok' AND album != '' "
                      "GROUP BY album_artist, album)"),
        "hi_quality": one("SELECT COUNT(*) FROM tracks WHERE status = 'ok' AND bitrate >= ?", min_bitrate),
        "multi": one("SELECT COUNT(*) FROM (SELECT pi.track_id FROM playlist_items pi JOIN playlist_sources ps "
                     "ON ps.id = pi.source_id GROUP BY pi.track_id HAVING COUNT(DISTINCT substr(ps.kind, 1, 3)) > 1)"),
        "upgradeable": len(pending),
        "below_threshold": len(below),
        "with_spotify": len(sp_ok),
        "min_bitrate": min_bitrate,
    }


def library_list(conn, view: str = "tracks", q: str = "", platform: str = "", offset: int = 0, limit: int = 100,
                 album: Optional[str] = None, album_artist: Optional[str] = None, artist: Optional[str] = None) -> dict:
    """Треки / альбомы / исполнители библиотеки с поиском и фильтром по площадке.
    album (+album_artist) / artist — точный фильтр (переход из альбома/исполнителя)."""
    _ulower(conn)
    where, args = ["t.status = 'ok'"], []
    if album is not None:
        where.append("t.album = ?")
        args.append(album)
        if album_artist is not None:
            where.append("t.album_artist = ?")
            args.append(album_artist)
    if artist is not None:
        where.append("t.artist = ?")
        args.append(artist)
    if q.strip():
        for word in q.lower().split():
            where.append("ulower(t.title || ' ' || t.artist || ' ' || t.album || ' ' || t.album_artist) LIKE ?")
            args.append(f"%{word}%")
    if platform in _PLATFORM_SQL:
        where.append(_PLATFORM_SQL[platform])
    w = " AND ".join(where)
    limit = max(1, min(int(limit), 500))
    if view == "albums":
        w += " AND t.album != ''"           # файлы без альбома — только во вкладке «Треки»
        total = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM tracks t WHERE {w} GROUP BY t.album_artist, t.album)",
                             args).fetchone()[0]
        rows = conn.execute(
            f"SELECT t.album_artist, t.album, COUNT(*) AS n, MAX(t.year) AS year, "
            f"ROUND(AVG(t.bitrate)) AS bitrate FROM tracks t WHERE {w} GROUP BY t.album_artist, t.album "
            f"ORDER BY ulower(t.album_artist), ulower(t.album) LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}
    if view == "artists":
        total = conn.execute(f"SELECT COUNT(DISTINCT t.artist) FROM tracks t WHERE {w}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT t.artist, COUNT(*) AS n, COUNT(DISTINCT t.album) AS albums FROM tracks t WHERE {w} "
            f"GROUP BY t.artist ORDER BY ulower(t.artist) LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}
    total = conn.execute(f"SELECT COUNT(*) FROM tracks t WHERE {w}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.* FROM tracks t WHERE {w} ORDER BY ulower(t.artist), ulower(t.album), "
        f"t.disc_number, t.track_number, ulower(t.title) LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    plats = track_platforms(conn, [r["id"] for r in rows])
    return {"total": total, "items": [_track_row(r, plats) for r in rows]}
