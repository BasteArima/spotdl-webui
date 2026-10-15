"""m3u-плейлисты из индекса (design/plan.md, задача 3.3).

Для плейлистов, которые синкает сервис (playlists.managed = 1), m3u пишет он
сам, а не spotdl: в файл попадают только скачанные треки, по одному разу, по
порядку источника — даже если трек пришёл под другим релизом или из другого
источника. Путь файла и формат — как у spotdl (`/music/playlists/<имя>.m3u8`,
`#EXTINF:<сек>,<исполнитель альбома> - <название>`, абсолютные пути), поэтому
Navidrome видит тот же плейлист, что и раньше.

Файл переписывается только при изменении содержимого — лишняя запись заставила
бы Navidrome переимпортировать плейлист.
"""
import os
from typing import Optional

from . import config
from .naming import safe_name


def path_for(name: str) -> str:
    return config.m3u_path(safe_name(name))


def render(conn, playlist_id: int) -> str:
    lines = ["#EXTM3U"]
    for r in conn.execute(
            "SELECT t.path, t.duration, t.title, t.artist, t.album_artist FROM playlist_items pi "
            "JOIN tracks t ON t.id = pi.track_id WHERE pi.playlist_id = ? AND t.status = 'ok' "
            "AND t.path IS NOT NULL GROUP BY t.id ORDER BY MIN(pi.source_id), MIN(pi.position), t.id",
            (playlist_id,)):
        who = r["album_artist"] or r["artist"]
        lines.append(f"#EXTINF:{int(round(r['duration'] or 0))},{who + ' - ' if who else ''}{r['title']}")
        lines.append(r["path"])
    return "\n".join(lines) + "\n"


def write_playlist(conn, playlist_id: int) -> bool:
    """Записать m3u плейлиста, если он ведётся сервисом и содержимое
    поменялось. Возвращает True, если файл переписан."""
    row = conn.execute("SELECT name, managed FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    if not row or not row["managed"]:
        return False
    path = path_for(row["name"])
    text = render(conn, playlist_id)
    old: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        pass
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return True


def write_managed(conn) -> int:
    """Все плейлисты, которые ведёт сервис. Возвращает, сколько файлов переписано."""
    ids = [r[0] for r in conn.execute("SELECT id FROM playlists WHERE managed = 1")]
    return sum(1 for pid in ids if write_playlist(conn, pid))
