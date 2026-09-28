"""Парсинг файлов errors/<safe>.txt, которые пишет spotdl через --save-errors.

Формат файла:
    первая строка — таймстамп (YYYY-MM-DD-HH-MM-SS)
    далее по строке на трек:
        SPOTIFY_TRACK_URL - ТИП_ОШИБКИ: текст[: ARTIST - TITLE]

Реальные примеры строк:
    https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb - LookupError: No results found for song: Arcane - Vow (feat. Ray Chen)
    https://open.spotify.com/track/… - AudioProviderError: YT-DLP download error - https://music.youtube.com/watch?v=…

Во втором случае трек НАЙДЕН, но yt-dlp его не скачал (лимит YouTube, cookies):
имени в строке нет — оно берётся из save-файла плейлиста, а найденная ссылка
YouTube отдаётся как source_url (повторить загрузку одной кнопкой).
"""
import glob
import json
import os
import re
import tempfile
from typing import Dict, List, Optional

from . import config, playlists

_URL_RE = re.compile(r"https?://\S+")


def _parse_track_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line or not line.lower().startswith("http"):
        return None
    # отделяем spotify-url от остального по первому ' - '
    parts = re.split(r"\s+-\s+", line, maxsplit=1)
    spotify_url = parts[0].strip()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    error_type = ""
    song = remainder
    source_url = ""
    if remainder.startswith("AudioProviderError"):
        m = _URL_RE.search(remainder)
        return {"spotify_url": spotify_url, "error_type": "AudioProviderError",
                "song": "", "source_url": m.group(0) if m else "", "raw": line}
    if remainder:
        # тип ошибки — до первого двоеточия
        if ":" in remainder:
            error_type = remainder.split(":", 1)[0].strip()
        else:
            error_type = remainder.strip()
        # "Artist - Title" обычно идёт после последнего ': '
        if ": " in remainder:
            song = remainder.rsplit(": ", 1)[-1].strip()
        else:
            song = ""
    return {
        "spotify_url": spotify_url,
        "error_type": error_type,
        "song": song,
        "source_url": source_url,
        "raw": line,
    }


def parse_errors_file(path: str) -> dict:
    """Возвращает {safe, playlist_file, timestamp, tracks: [...]}.

    spotdl пишет --save-errors в режиме ДОЗАПИСИ: каждый скан добавляет блок
    «таймстамп + ненайденные треки» в конец файла, поэтому при повторных сканах
    один и тот же трек встречается многократно. Здесь дедуплицируем треки по
    Spotify-URL (оставляем первое вхождение) и берём последний таймстамп."""
    safe = os.path.splitext(os.path.basename(path))[0]
    timestamp = ""
    tracks: List[dict] = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        parsed = _parse_track_line(line)
        if parsed:
            key = parsed["spotify_url"]
            if key in seen:
                continue
            seen.add(key)
            tracks.append(parsed)
        elif not s.lower().startswith("http"):
            # строка-таймстамп (их может быть несколько — берём самую свежую)
            timestamp = s
    return {
        "safe": safe,
        "file": os.path.basename(path),
        "timestamp": timestamp,
        "tracks": tracks,
    }


_names_cache: Dict[str, tuple] = {}   # путь save-файла -> (mtime, {url: имя})


def _savefile_names(path: str) -> Dict[str, str]:
    """{spotify-url: "Артист - Название"} из save-файла spotdl (кэш по mtime:
    у Liked Songs он на мегабайт)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _names_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    names = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for s in playlists.savefile_songs(json.load(fh)):
                if isinstance(s, dict) and s.get("url") and s.get("name"):
                    names[s["url"]] = f"{s.get('artist') or ''} - {s['name']}".strip(" -")
    except (OSError, ValueError, AttributeError):
        return {}
    _names_cache[path] = (mtime, names)
    return names


def list_errors() -> List[dict]:
    """Все errors/*.txt, сгруппированные по плейлисту (safe-имя файла).
    Треки без имени (ошибка загрузки) получают его из save-файла плейлиста."""
    pattern = os.path.join(config.ERRORS_DIR, "*.txt")
    out = []
    ids = None
    for path in sorted(glob.glob(pattern)):
        data = parse_errors_file(path)
        if not data["tracks"]:
            continue
        if any(not t["song"] for t in data["tracks"]):
            if ids is None:
                ids = {p.safe: p.id for p in playlists.read_playlists()}
            pid = ids.get(data["safe"])
            names = _savefile_names(config.savefile_path(pid)) if pid else {}
            for t in data["tracks"]:
                if not t["song"]:
                    t["song"] = names.get(t["spotify_url"], "")
        out.append(data)
    return out


def remove_track(safe: str, spotify_url: str) -> bool:
    """Убрать строку трека из errors/<safe>.txt после успешного добивания.
    Таймстамп и прочие строки сохраняются. Возвращает True, если что-то удалили."""
    path = config.errors_path(safe)
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    kept = []
    removed = False
    for line in lines:
        parsed = _parse_track_line(line)
        if parsed and parsed["spotify_url"] == spotify_url:
            removed = True
            continue
        kept.append(line)

    if not removed:
        return False

    # если остался только таймстамп (или пусто) — удаляем файл целиком
    meaningful = [ln for ln in kept if _parse_track_line(ln)]
    d = os.path.dirname(path) or "."
    if not meaningful:
        try:
            os.remove(path)
        except OSError:
            pass
        return True

    content = "\n".join(kept)
    if content and not content.endswith("\n"):
        content += "\n"
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".err.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return True
