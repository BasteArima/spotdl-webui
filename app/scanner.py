"""Сканер библиотеки: теги файлов /music → индекс (app/db.py).

Индекс для уже скачанной библиотеки собирается без единого запроса к
площадкам: spotdl (и наш place_file для Deezer/librespot/своих файлов) пишет в
каждый файл ссылку на трек Spotify (mp3: WOAS), ссылку на ролик YouTube, с
которого взят звук (mp3: COMM), и ISRC (mp3: TSRC). Из них получаются внешние
ID трека — по ним потом склеиваются дубли между площадками.

Инкрементально: файл с прежними mtime и размером не перечитывается (повторный
скан библиотеки — секунды). --full перечитывает всё.

Запуск отдельным процессом (mutagen и обход тысяч файлов не нужны веб-процессу):
    python -m app.scanner [--full]      → прогресс строками, итог — JSON последней строкой
"""
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

from . import config, db

AUDIO_EXT = {".mp3", ".m4a", ".mp4", ".flac", ".opus", ".ogg", ".oga", ".wav", ".aac", ".wma"}
TRASH_DIR = ".trash"          # корзина сервиса (склейка дублей) — не индексируется
BATCH = 300                   # файлов на транзакцию: веб успевает читать между ними

_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
_YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_DEEZER_RE = re.compile(r"deezer\.com/(?:[a-z]{2}/)?track/(\d+)")
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
_ID_LABEL = {"spotify": "трек Spotify", "youtube": "ролик YouTube", "deezer": "трек Deezer"}


# ------------------------------------------------------------------ чтение тегов
def _first(v) -> str:
    """Первое текстовое значение тега (mutagen отдаёт списки/фреймы)."""
    if v is None:
        return ""
    if hasattr(v, "text"):                 # ID3-фрейм
        v = v.text
    if hasattr(v, "url"):                  # WOAS и прочие URL-фреймы
        return str(v.url)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):               # MP4 freeform (----:spotdl:WOAS)
        v = v.decode("utf-8", "replace")
    if isinstance(v, tuple):               # MP4 trkn/disk: (номер, всего)
        v = v[0]
    return str(v).strip()


def _num(s) -> Optional[int]:
    """'3/12' → 3; (3, 12) → 3; мусор → None."""
    if isinstance(s, (list, tuple)) and s:
        s = s[0]
    if isinstance(s, tuple):
        s = s[0]
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def split_artists(raw: str, album_artist: str = "") -> List[str]:
    """spotdl склеивает исполнителей через «/» (ID3 v2.3 не умеет списков), но «/»
    бывает и в имени (HUNTR/X, AC/DC). Исполнитель альбома пишется отдельно и не
    склеивается — если строка с него начинается, он один исполнитель целиком."""
    raw = (raw or "").strip()
    if not raw:
        return []
    aa = (album_artist or "").strip()
    head: List[str] = []
    if aa and "/" in aa and (raw == aa or raw.startswith(aa + "/")):
        head, raw = [aa], raw[len(aa) + 1:]
    parts = [p.strip() for p in raw.split("/") if p.strip()] if raw else []
    return head + parts


def _ids_from(*texts: str) -> Dict[str, str]:
    """Внешние ID из ссылок в тегах: spotify, youtube, deezer."""
    ids: Dict[str, str] = {}
    for t in texts:
        if not t:
            continue
        for kind, rx in (("spotify", _SPOTIFY_RE), ("youtube", _YOUTUBE_RE), ("deezer", _DEEZER_RE)):
            m = rx.search(t)
            if m and kind not in ids:
                ids[kind] = m.group(1)
    return ids


def read_tags(path: str) -> dict:
    """Метаданные и внешние ID одного файла. Не прочитался — по имени файла."""
    import mutagen  # noqa: WPS433 — тяжёлый импорт только в процессе сканера

    rec = {"title": "", "artist": "", "artists": [], "album": "", "album_artist": "",
           "track_number": None, "disc_number": None, "year": None, "duration": None,
           "isrc": None, "codec": os.path.splitext(path)[1].lstrip(".").lower(),
           "bitrate": None, "ids": {}, "error": ""}
    try:
        f = mutagen.File(path)
    except Exception as e:  # noqa: BLE001 — битый файл не должен ронять скан
        f, rec["error"] = None, f"{type(e).__name__}: {e}"
    if f is not None:
        info = getattr(f, "info", None)
        if info is not None:
            rec["duration"] = round(float(getattr(info, "length", 0) or 0), 2) or None
            br = getattr(info, "bitrate", 0) or 0
            rec["bitrate"] = int(round(br / 1000)) if br else None
        tags = f.tags or {}
        kind = type(f).__name__        # MP3 / MP4 / FLAC / OggOpus / OggVorbis …
        get = lambda k: tags.get(k) if hasattr(tags, "get") else None  # noqa: E731
        if kind == "MP3" or rec["codec"] == "mp3":
            comments = [_first(v) for k, v in tags.items() if k.startswith("COMM")] if tags else []
            urls = [_first(v) for k, v in tags.items() if k.startswith(("WOAS", "WXXX"))] if tags else []
            tpe1 = get("TPE1")
            # v2.3 (так пишет spotdl) — одна строка «A/B», v2.4 — список значений
            title, artist_raw = _first(get("TIT2")), "/".join(str(x) for x in (tpe1.text if tpe1 else []))
            album, album_artist = _first(get("TALB")), _first(get("TPE2"))
            trck, tpos, year, isrc = get("TRCK"), get("TPOS"), _first(get("TDRC")), _first(get("TSRC"))
        elif kind == "MP4":
            comments = [_first(get("\xa9cmt"))]
            urls = [_first(get("----:spotdl:WOAS"))]
            title, artist_raw = _first(get("\xa9nam")), "/".join(str(x) for x in (get("\xa9ART") or []))
            album, album_artist = _first(get("\xa9alb")), _first(get("aART"))
            trck, tpos, year = get("trkn"), get("disk"), _first(get("\xa9day"))
            isrc = _first(get("----:spotdl:ISRC"))
        else:                           # Vorbis-комментарии (flac, ogg, opus)
            comments = [_first(get("comment")), _first(get("download_url"))]
            urls = [_first(get("woas")), _first(get("url"))]
            title, artist_raw = _first(get("title")), "/".join(get("artist") or [])
            album, album_artist = _first(get("album")), _first(get("albumartist"))
            trck, tpos, year, isrc = get("tracknumber"), get("discnumber"), _first(get("date")), _first(get("isrc"))
        rec.update(title=title, album=album, album_artist=album_artist,
                   track_number=_num(trck), disc_number=_num(tpos), year=_num(year[:4] if year else ""))
        rec["artists"] = split_artists(artist_raw, album_artist)
        rec["artist"] = rec["artists"][0] if rec["artists"] else album_artist
        isrc = (isrc or "").strip().upper().replace("-", "")
        rec["isrc"] = isrc if _ISRC_RE.match(isrc) else None
        rec["ids"] = _ids_from(*urls, *comments)
    if not rec["bitrate"] and rec["duration"]:
        # opus и часть ogg не отдают битрейт в заголовке — средний по размеру
        try:
            rec["bitrate"] = int(round(os.path.getsize(path) * 8 / rec["duration"] / 1000)) or None
        except OSError:
            pass
    if not rec["title"]:
        rec["title"] = os.path.splitext(os.path.basename(path))[0]
    return rec


# ------------------------------------------------------------------ обход
def iter_audio_files(root: str):
    """Аудиофайлы библиотеки, кроме папки m3u-плейлистов, корзины и скрытых."""
    skip = {os.path.normcase(os.path.abspath(config.PLAYLISTS_M3U_DIR))}
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name == TRASH_DIR:
                continue
            if e.is_dir(follow_symlinks=False):
                if os.path.normcase(os.path.abspath(e.path)) not in skip:
                    stack.append(e.path)
            elif os.path.splitext(e.name)[1].lower() in AUDIO_EXT:
                yield e


def _save(conn, path: str, st, rec: dict, existing_id: Optional[int], now: float,
          conflicts: List[dict]) -> None:
    fields = dict(path=path, status="ok", title=rec["title"], artist=rec["artist"],
                  artists=json.dumps(rec["artists"], ensure_ascii=False), album=rec["album"],
                  album_artist=rec["album_artist"], track_number=rec["track_number"],
                  disc_number=rec["disc_number"], year=rec["year"], duration=rec["duration"],
                  isrc=rec["isrc"], codec=rec["codec"], bitrate=rec["bitrate"],
                  file_size=st.st_size, file_mtime=st.st_mtime, updated_at=now)
    if existing_id:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE tracks SET {cols} WHERE id = ?", (*fields.values(), existing_id))
        track_id = existing_id
        # ID из тегов переписываем заново (файл мог перетегироваться); ID из
        # других источников (синк, поиск) не трогаем
        conn.execute("DELETE FROM track_ids WHERE track_id = ? AND origin = 'tag'", (track_id,))
    else:
        fields["added_at"] = now
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        track_id = conn.execute(f"INSERT INTO tracks({cols}) VALUES ({marks})",
                                tuple(fields.values())).lastrowid
    for kind, value in rec["ids"].items():
        conn.execute("INSERT OR IGNORE INTO track_ids(kind, value, track_id, origin) "
                     "VALUES (?, ?, ?, 'tag')", (kind, value, track_id))
        owner = conn.execute("SELECT track_id FROM track_ids WHERE kind = ? AND value = ?",
                             (kind, value)).fetchone()[0]
        if owner != track_id:
            # два файла с одним внешним ID — почти наверняка один трек дважды.
            # Запоминаем пару в кандидаты (уровень 1 — точное совпадение по ID);
            # склеивать или нет — решит сопоставление (задача 1.4) / пользователь
            a, b = sorted((track_id, owner))
            conn.execute("INSERT OR IGNORE INTO match_candidates(track_a, track_b, score, level, "
                         "reason, created_at) VALUES (?, ?, 100, 1, ?, ?)",
                         (a, b, f"один и тот же {_ID_LABEL[kind]}", now))
            if len(conflicts) < 200:
                conflicts.append({"kind": kind, "value": value, "track_id": track_id, "other_id": owner})


def scan(music_dir: Optional[str] = None, db_path: Optional[str] = None, full: bool = False,
         progress=None) -> dict:
    """Синхронизировать индекс с файлами. Возвращает итог (и пишет его в meta)."""
    music_dir = music_dir or config.MUSIC_DIR
    t0 = time.time()
    res = {"files": 0, "new": 0, "updated": 0, "unchanged": 0, "vanished": 0,
           "kept_missing": 0, "unreadable": 0, "with_spotify": 0, "with_youtube": 0,
           "with_isrc": 0, "id_conflicts": 0}
    conflicts: List[dict] = []
    unreadable: List[str] = []
    with db.connect(db_path) as conn:
        existing: Dict[str, Tuple[int, Optional[float], Optional[int]]] = {
            r["path"]: (r["id"], r["file_mtime"], r["file_size"])
            for r in conn.execute("SELECT id, path, file_mtime, file_size FROM tracks WHERE path IS NOT NULL")}
        seen = set()
        pending = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in iter_audio_files(music_dir):
                path = os.path.abspath(entry.path)
                try:
                    st = entry.stat()
                except OSError:
                    continue
                seen.add(path)
                res["files"] += 1
                old = existing.get(path)
                if old and not full and old[1] == st.st_mtime and old[2] == st.st_size:
                    res["unchanged"] += 1
                else:
                    rec = read_tags(path)
                    if rec["error"]:
                        res["unreadable"] += 1
                        if len(unreadable) < 50:
                            unreadable.append(f"{path}: {rec['error']}")
                    _save(conn, path, st, rec, old[0] if old else None, time.time(), conflicts)
                    res["updated" if old else "new"] += 1
                    pending += 1
                if pending >= BATCH:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN IMMEDIATE")
                    pending = 0
                if progress and res["files"] % 500 == 0:
                    progress(res["files"])
            # файлы, которых больше нет на диске
            for path, (tid, _m, _s) in existing.items():
                if path in seen:
                    continue
                res["vanished"] += 1
                in_playlist = conn.execute("SELECT 1 FROM playlist_items WHERE track_id = ? LIMIT 1",
                                           (tid,)).fetchone()
                if in_playlist:
                    # трек нужен плейлисту — остаётся «не скачанным», а не исчезает
                    conn.execute("UPDATE tracks SET path = NULL, status = 'missing', updated_at = ? "
                                 "WHERE id = ?", (time.time(), tid))
                    conn.execute("INSERT OR IGNORE INTO missing(track_id, reason, detail) "
                                 "VALUES (?, 'other', 'файл пропал с диска')", (tid,))
                    res["kept_missing"] += 1
                else:
                    conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        res["with_spotify"] = one("SELECT COUNT(DISTINCT track_id) FROM track_ids WHERE kind = 'spotify'")
        res["with_youtube"] = one("SELECT COUNT(DISTINCT track_id) FROM track_ids WHERE kind = 'youtube'")
        res["with_isrc"] = one("SELECT COUNT(*) FROM tracks WHERE isrc IS NOT NULL AND status = 'ok'")
        res["id_conflicts"] = len(conflicts)
        res["seconds"] = round(time.time() - t0, 1)
        res["finished_at"] = time.time()
        with db.transaction(conn):
            db.set_meta(conn, "last_scan", json.dumps(res))
    res["conflicts_sample"] = conflicts[:20]
    res["unreadable_sample"] = unreadable[:20]
    return res


def last_scan(db_path: Optional[str] = None) -> Optional[dict]:
    with db.connect(db_path) as conn:
        raw = db.get_meta(conn, "last_scan")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _cli() -> None:
    full = "--full" in sys.argv[1:]
    print(f"[scan] {'полный' if full else 'инкрементальный'} скан {config.MUSIC_DIR}", flush=True)
    try:
        res = scan(full=full, progress=lambda n: print(f"[scan] просмотрено файлов: {n}", flush=True))
    except Exception as e:  # noqa: BLE001 — веб должен получить JSON, а не трейсбек
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}), flush=True)
        sys.exit(1)
    for line in res.pop("unreadable_sample", []):
        print(f"[scan] не прочитался: {line}", flush=True)
    res.pop("conflicts_sample", None)
    print(f"[scan] файлов {res['files']}: новых {res['new']}, изменилось {res['updated']}, "
          f"без изменений {res['unchanged']}, пропало {res['vanished']} · "
          f"со ссылкой Spotify {res['with_spotify']}, YouTube {res['with_youtube']}, "
          f"ISRC {res['with_isrc']} · общих ID у разных файлов {res['id_conflicts']} · "
          f"{res['seconds']} с", flush=True)
    print(json.dumps(dict(res, ok=True)), flush=True)


if __name__ == "__main__":
    _cli()
