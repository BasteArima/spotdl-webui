"""Парсинг файлов errors/<safe>.txt, которые пишет spotdl через --save-errors.

Формат файла:
    первая строка — таймстамп (YYYY-MM-DD-HH-MM-SS)
    далее по строке на трек:
        SPOTIFY_TRACK_URL - ТИП_ОШИБКИ: текст[: ARTIST - TITLE]

Реальный пример строки:
    https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb - LookupError: No results found for song: Arcane - Vow (feat. Ray Chen)
"""
import glob
import os
import re
import tempfile
from typing import List, Optional

from . import config


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
        "raw": line,
    }


def parse_errors_file(path: str) -> dict:
    """Возвращает {safe, playlist_file, timestamp, tracks: [...]}."""
    safe = os.path.splitext(os.path.basename(path))[0]
    timestamp = ""
    tracks: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        if i == 0 and not line.strip().lower().startswith("http"):
            timestamp = line.strip()
            continue
        parsed = _parse_track_line(line)
        if parsed:
            tracks.append(parsed)
    return {
        "safe": safe,
        "file": os.path.basename(path),
        "timestamp": timestamp,
        "tracks": tracks,
    }


def list_errors() -> List[dict]:
    """Все errors/*.txt, сгруппированные по плейлисту (safe-имя файла)."""
    pattern = os.path.join(config.ERRORS_DIR, "*.txt")
    out = []
    for path in sorted(glob.glob(pattern)):
        data = parse_errors_file(path)
        if data["tracks"]:
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
