"""Чтение/запись playlists.txt в формате `Имя|ссылка`.

Парсинг строк воспроизводит логику bash автосинка:
    name = до первого '|', с обрезкой ХВОСТОВЫХ пробелов
    url  = всё после первого '|', с обрезкой НАЧАЛЬНЫХ пробелов
Строки, начинающиеся с '#', и пустые — игнорируются.
"""
import os
import tempfile
from typing import List, Optional

from . import config
from .naming import SAVED_QUERY, is_saved, playlist_id, safe_name, url_type


class Playlist:
    def __init__(self, name: str, url: str, raw_index: int):
        self.name = name
        self.url = url
        self.raw_index = raw_index  # индекс непустой/незакомментированной записи

    @property
    def safe(self) -> str:
        return safe_name(self.name)

    @property
    def id(self) -> str:
        return playlist_id(self.url)

    @property
    def type(self) -> str:
        return url_type(self.url)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "safe": self.safe,
            "id": self.id,
            "type": self.type,
        }


def _parse_line(line: str) -> Optional[tuple]:
    """Возвращает (name, url) либо None для пустых/комментариев/без '|'."""
    if not line:
        return None
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "|" not in line:
        return None
    name_part, url_part = line.split("|", 1)
    name = name_part.rstrip()          # trim trailing whitespace
    url = url_part.strip()             # trim leading (и хвостовой \n) whitespace
    if not url:
        return None
    return name, url


def read_playlists() -> List[Playlist]:
    path = config.PLAYLISTS_FILE
    result: List[Playlist] = []
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as fh:
        idx = 0
        for line in fh:
            parsed = _parse_line(line.rstrip("\n"))
            if parsed is None:
                continue
            name, url = parsed
            result.append(Playlist(name, url, idx))
            idx += 1
    return result


def _write_all(lines: List[str]) -> None:
    """Атомарная перезапись playlists.txt (сохраняем перевод строки в конце)."""
    path = config.PLAYLISTS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".playlists.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _raw_lines() -> List[str]:
    """Все строки файла как есть (для сохранения комментариев при правках)."""
    path = config.PLAYLISTS_FILE
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh]


def _normalize_url(url: str) -> str:
    """`Saved`/` saved ` → канонический `saved`: от строки считается id
    save-файла, поэтому написание должно быть одно."""
    url = url.strip()
    return SAVED_QUERY if is_saved(url) else url


def add_playlist(name: str, url: str) -> Playlist:
    name = name.rstrip()
    url = _normalize_url(url)
    if not name or not url:
        raise ValueError("Имя и ссылка обязательны")
    if "|" in name:
        raise ValueError("Имя не может содержать символ '|'")
    # запрет дубля по URL
    for p in read_playlists():
        if p.url == url:
            raise ValueError("Плейлист с такой ссылкой уже есть")
    lines = _raw_lines()
    lines.append(f"{name}|{url}")
    _write_all(lines)
    return Playlist(name, url, len(read_playlists()) - 1)


def _find_raw_index(url: str) -> Optional[int]:
    """Индекс строки в сыром файле, где запись имеет данный url."""
    for i, ln in enumerate(_raw_lines()):
        parsed = _parse_line(ln)
        if parsed and parsed[1] == url:
            return i
    return None


def delete_playlist(url: str) -> bool:
    lines = _raw_lines()
    idx = _find_raw_index(url)
    if idx is None:
        return False
    del lines[idx]
    _write_all(lines)
    return True


def update_playlist(old_url: str, name: str, url: str) -> Playlist:
    name = name.rstrip()
    url = _normalize_url(url)
    if not name or not url:
        raise ValueError("Имя и ссылка обязательны")
    if "|" in name:
        raise ValueError("Имя не может содержать символ '|'")
    lines = _raw_lines()
    idx = _find_raw_index(old_url)
    if idx is None:
        raise ValueError("Запись не найдена")
    # если меняется url — не должно совпасть с другой записью
    if url != old_url:
        for p in read_playlists():
            if p.url == url:
                raise ValueError("Плейлист с такой ссылкой уже есть")
    lines[idx] = f"{name}|{url}"
    _write_all(lines)
    return Playlist(name, url, idx)


def find_by_safe(safe: str) -> Optional[Playlist]:
    """Найти плейлист, safe-имя которого совпадает с safe (имя errors-файла)."""
    for p in read_playlists():
        if p.safe == safe:
            return p
    return None


def find_by_url(url: str) -> Optional[Playlist]:
    for p in read_playlists():
        if p.url == url:
            return p
    return None
