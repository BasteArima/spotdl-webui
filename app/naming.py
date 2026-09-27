"""Воспроизведение правил очистки имени и вычисления id один-в-один с
bash/sed автосинка. Любое расхождение разведёт папки и плейлисты, поэтому
эти функции покрыты тестами (tests/test_naming.py)."""
import hashlib
import re

# Символы, которые автосинк заменяет на пробел:  / \ : * ? " < > |
_BAD_CHARS = re.compile(r'[/\\:*?"<>|]')
_MULTISPACE = re.compile(r" +")
# /(playlist|album|track|artist)/ в spotify-ссылке
_TYPE_RE = re.compile(r"open\.spotify\.com/(playlist|album|track|artist)/", re.IGNORECASE)
# Спецзапрос spotdl для «Любимых треков» (Liked Songs): ссылки у них нет.
SAVED_QUERY = "saved"


def is_saved(url: str) -> bool:
    """Запись плейлиста — это Liked Songs (spotdl-запрос `saved`)?"""
    return (url or "").strip().lower() == SAVED_QUERY


def safe_name(name: str) -> str:
    """Эквивалент sed:
        s#[/\\:*?"<>|]# #g; s/  */ /g; s/[[:space:]]*$//
    с предварительным trim_right имени.

    Формула из ТЗ: trim_right(name) -> replace -> collapse_spaces -> trim_right
    Кириллица/японский и прочие символы сохраняются — заменяются только
    перечисленные ASCII-символы.
    """
    s = name.rstrip()
    s = _BAD_CHARS.sub(" ", s)
    s = _MULTISPACE.sub(" ", s)
    s = s.rstrip()
    return s


def playlist_id(url: str) -> str:
    """id = md5(url) | cut -c1-12 — как в автосинке (--save-file /conf/$id.spotdl).
    md5 считается от строки url БЕЗ завершающего перевода строки (printf '%s')."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def url_type(url: str) -> str:
    """playlist / album / track / artist / saved / unknown."""
    if is_saved(url):
        return "saved"
    m = _TYPE_RE.search(url or "")
    return m.group(1).lower() if m else "unknown"
