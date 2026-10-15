"""Выборки из индекса библиотеки для нового интерфейса (app/static/v2).

Только чтение. Все функции берут открытое соединение (app/db.connect) и
возвращают простые словари — их отдают /api/v2/* как есть.
"""
import json
from typing import Dict, List

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
