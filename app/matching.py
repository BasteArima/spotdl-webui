"""Сопоставление треков: та же ли это запись (design/plan.md, §4).

Дубль — ТА ЖЕ запись с разных площадок или из разных плейлистов. Ремастер,
live, кавер, «Russian ver.», slowed — разные треки (решение пользователя):
маркеры версии из названия не вырезаются, а сравниваются.

Уровни (от точного к нечёткому):
  1  общий внешний ID (тот же ролик YouTube / трек Spotify)    → склеить сразу
  2  одинаковый ISRC                                           → склеить сразу
  3  основа названия + маркеры совпали, исполнитель общий,
     длительность ±3 с                                         → склеить сразу
  4  похоже, но не точно                                       → спросить, с процентом

Только стандартная библиотека. NORM_VERSION — версия правил нормализации:
поменяли правила → увеличить, и norm_key в индексе пересчитаются.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Set, Tuple

NORM_VERSION = 1

# ------------------------------------------------------------------ разбор названия
_BRACKETS = re.compile(r"[(\[{（［【]([^)\]}）］】]*)[)\]}）］】]")
_DASH_SPLIT = re.compile(r"\s+[-–—―]\s+")

# соавторы — исполнители сравниваются отдельно
_FEAT = re.compile(r"^(?:feat\.?|ft\.?|featuring|with|prod\.?(?:\s+by)?)\s", re.I)
# пометки площадки, не меняющие запись
_NOISE = re.compile(
    r"^(?:official\s+)?(?:music\s+|lyrics?\s+|hd\s+|4k\s+)?(?:video|audio|visuali[sz]er|mv|clip)$"
    r"|^official$|^lyrics?$|^with\s+lyrics$|^hd$|^hq$|^4k$|^explicit(?:\s+ver(?:sion)?\.?)?$"
    r"|^original(?:\s+mix|\s+version)?$|^album\s+version$|^single$|^bonus\s+track$|^audio\s+only$", re.I)
# контекст: откуда трек (сериал, игра, фильм) — та же запись
_CONTEXT = re.compile(r"^from\b|\bsoundtrack\b|\bfrom\s+the\s+(?:series|motion\s+picture|film|movie|game)\b"
                      r"|\boriginal\s+motion\s+picture\b", re.I)
# маркеры версии — другая запись
_MARKER = re.compile(
    r"\b(?:live|remix|rmx|mix|cover|acoustic|instrumental|karaoke|remaster(?:ed)?|slowed|sped\s*up"
    r"|speed\s*up|reverb|nightcore|edit|extended|demo|version|ver|unplugged|rework|bootleg|mashup"
    r"|tv\s*size|tv-size|piano|orchestral|orchestra|epic|cinematic|8d|clean|radio)\b", re.I)


def _norm(s: str) -> str:
    """Регистр, полноширинные символы, апострофы; всё не-буквенное — в пробелы.
    Кириллица и японский остаются как есть (\\w в Python — юникодный)."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[’‘`´ʼ]", "'", s).replace("'", "")
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _marker_form(seg: str) -> str:
    """Каноническая запись маркера: 'Remastered 2023' → 'remaster',
    'Russian ver.' → 'russian version'."""
    s = _norm(seg)
    s = re.sub(r"\bver\b", "version", s)
    s = re.sub(r"\bremastered\b", "remaster", s)
    if "remaster" in s:
        s = re.sub(r"\b(?:19|20)\d\d\b", "", s)           # год ремастера неважен
    s = re.sub(r"\bsped up\b|\bspeed up\b", "sped up", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_title(title: str) -> Tuple[str, Tuple[str, ...]]:
    """Название → (основа, маркеры версии). Скобки и части через « - »
    классифицируются: соавторы/пометки/контекст отбрасываются, маркеры
    собираются, прочее остаётся частью основы."""
    t = unicodedata.normalize("NFKC", title or "")
    segs: List[str] = [m.strip() for m in _BRACKETS.findall(t)]
    t = _BRACKETS.sub(" ", t)
    parts = _DASH_SPLIT.split(t.strip())
    main, segs = parts[0], [p.strip() for p in parts[1:]] + segs
    base_extra: List[str] = []
    markers: List[str] = []
    for seg in segs:
        if not seg:
            continue
        if _FEAT.match(seg) or _NOISE.match(seg.strip(" .")) or _CONTEXT.search(seg):
            continue
        if _MARKER.search(seg):
            markers.append(_marker_form(seg))
        else:
            base_extra.append(seg)
    # «Title feat. X» без скобок
    main = re.split(r"\s(?:feat\.?|ft\.?|featuring)\s", main, maxsplit=1, flags=re.I)[0]
    base = _norm(" ".join([main] + base_extra))
    if not base:                      # название целиком из скобок
        base = _norm(title)
    return base, tuple(sorted(set(m for m in markers if m)))


def title_key(title: str) -> str:
    """Ключ для индекса (tracks.norm_key): 'основа|маркер+маркер'."""
    base, markers = parse_title(title)
    return f"{base}|{'+'.join(markers)}"


# ------------------------------------------------------------------ исполнители
def artist_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").lower().strip()
    s = re.sub(r"\s*-\s*topic$", "", s)                   # автоканалы YouTube Music
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^\w]+", "", s)
    s = re.sub(r"(?:vevo|official|music)$", "", s) or s   # «JohnnyCashVEVO»
    return s


def artist_keys(artists: Iterable[str]) -> Set[str]:
    return {k for k in (artist_key(a) for a in artists or []) if k}


def artists_overlap(a: Iterable[str], b: Iterable[str]) -> bool:
    """Общий исполнитель. Учитывает «AC/DC», разрезанное spotdl на «AC» и «DC»:
    склейка ключей одной стороны совпадает с ключом другой."""
    ka, kb = artist_keys(a), artist_keys(b)
    if not ka or not kb:
        return False
    if ka & kb:
        return True
    ja, jb = "".join(sorted(ka)), "".join(sorted(kb))
    return any(len(k) >= 3 and (k == jb or k in jb) for k in ka) or \
        any(len(k) >= 3 and (k == ja or k in ja) for k in kb)


# ------------------------------------------------------------------ сравнение
def compare(a: dict, b: dict) -> Optional[Tuple[int, int, str]]:
    """Та же ли запись. a/b: {title, artists, duration, isrc, ids: {kind: value}}.
    Возвращает (уровень, процент, причина) или None — разные треки."""
    ids_a, ids_b = a.get("ids") or {}, b.get("ids") or {}
    for kind, label in (("youtube", "один и тот же ролик YouTube"), ("spotify", "один и тот же трек Spotify"),
                        ("deezer", "один и тот же трек Deezer")):
        if ids_a.get(kind) and ids_a.get(kind) == ids_b.get(kind):
            return 1, 100, label
    if a.get("isrc") and a.get("isrc") == b.get("isrc"):
        return 2, 99, "одинаковый ISRC"

    base_a, mk_a = parse_title(a.get("title", ""))
    base_b, mk_b = parse_title(b.get("title", ""))
    if mk_a != mk_b:
        return None                  # live / ремастер / кавер… — разные записи
    overlap = artists_overlap(a.get("artists") or [], b.get("artists") or [])
    da, db_ = a.get("duration"), b.get("duration")
    dd = abs(da - db_) if da and db_ else None

    if base_a == base_b:
        if overlap and dd is not None and dd <= 3:
            return 3, 95, "название, исполнитель и длительность совпали"
        if overlap and dd is None:
            return 4, 85, "название и исполнитель совпали, длительность неизвестна"
        if overlap and dd <= 10:
            return 4, 80, f"название и исполнитель совпали, длительность отличается на {int(round(dd))} с"
        if not overlap and dd is not None and dd <= 3:
            return 4, 70, "название и длительность совпали, исполнитель другой"
        return None
    if overlap and (dd is None or dd <= 3):
        ratio = SequenceMatcher(None, base_a, base_b).ratio()
        if ratio >= 0.85:
            return 4, int(ratio * 90), f"похожее название ({int(ratio * 100)}%), исполнитель совпал"
    return None
