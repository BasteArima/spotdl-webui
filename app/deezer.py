"""Скачивание трека с Deezer по ISRC (точное совпадение записи со Spotify).

Логика та же, что у deemix/streamrip: gw-light авторизация по ARL → получение
media-URL → скачивание зашифрованного потока → расшифровка Blowfish (BF_CBC_STRIPE).
Нужен только requests + pycryptodome (без тяжёлых зависимостей).

ARL — это секрет уровня пароля от Deezer-аккаунта; подаётся через окружение
(DEEZER_ARL) или файл (/conf/deezer_arl.txt), в образ/репозиторий не попадает.
"""
import hashlib
import os
from typing import Optional, Tuple

import requests

_SECRET = b"g4el58wc0zvf9na1"
_GW = "https://www.deezer.com/ajax/gw-light.php"
_GET_URL = "https://media.deezer.com/v1/get_url"
_PUBLIC = "https://api.deezer.com"

# Форматы в порядке приоритета. Free-аккаунт получит MP3_128, Premium/HiFi — выше.
_FORMATS = [
    {"cipher": "BF_CBC_STRIPE", "format": "FLAC"},
    {"cipher": "BF_CBC_STRIPE", "format": "MP3_320"},
    {"cipher": "BF_CBC_STRIPE", "format": "MP3_128"},
]
_EXT = {"FLAC": ".flac", "MP3_320": ".mp3", "MP3_256": ".mp3", "MP3_128": ".mp3"}


class DeezerError(Exception):
    pass


def resolve_isrc(isrc: str) -> Optional[int]:
    """ISRC → Deezer track id (публичный API). None, если не найдено."""
    if not isrc:
        return None
    try:
        r = requests.get(f"{_PUBLIC}/track/isrc:{isrc}", timeout=15)
        j = r.json()
    except Exception as e:  # noqa: BLE001
        raise DeezerError(f"Deezer API недоступен: {e}") from e
    if isinstance(j, dict) and j.get("id"):
        return int(j["id"])
    return None


def search_track(artist: str, title: str) -> Optional[int]:
    """Найти трек на Deezer по исполнителю+названию (фолбэк, когда нет ISRC).
    Возвращает Deezer track id или None."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        return None
    queries = []
    if artist:
        queries.append(f'artist:"{artist}" track:"{title}"')
        queries.append(f"{artist} {title}")
    else:
        queries.append(title)
    for q in queries:
        try:
            r = requests.get(f"{_PUBLIC}/search", params={"q": q, "limit": 5}, timeout=15)
            data = r.json().get("data") or []
        except Exception:  # noqa: BLE001
            continue
        if data:
            return int(data[0]["id"])
    return None


def _blowfish_key(sng_id) -> bytes:
    h = hashlib.md5(str(sng_id).encode()).hexdigest().encode()  # 32 hex-байта
    return bytes(h[i] ^ h[i + 16] ^ _SECRET[i] for i in range(16))


def _decrypt_to(resp: requests.Response, sng_id, out_path: str) -> None:
    """BF_CBC_STRIPE: каждый 3-й полный блок 2048 байт расшифровывается Blowfish-CBC."""
    from Crypto.Cipher import Blowfish  # импорт здесь, чтобы модуль грузился без него

    key = _blowfish_key(sng_id)
    iv = bytes(range(8))  # 00 01 02 03 04 05 06 07
    with open(out_path, "wb") as fh:
        for i, chunk in enumerate(resp.iter_content(2048)):
            if len(chunk) == 2048 and i % 3 == 0:
                chunk = Blowfish.new(key, Blowfish.MODE_CBC, iv).decrypt(chunk)
            fh.write(chunk)


def _gw_auth(session: requests.Session, arl: str) -> Tuple[str, str]:
    session.cookies.set("arl", arl, domain=".deezer.com")
    r = session.post(_GW, params={
        "method": "deezer.getUserData", "input": "3",
        "api_version": "1.0", "api_token": "",
    }, timeout=15)
    results = r.json().get("results") or {}
    user = results.get("USER") or {}
    if not user.get("USER_ID"):
        raise DeezerError("ARL недействителен или истёк (Deezer не авторизовал)")
    api_token = results.get("checkForm")
    license_token = (user.get("OPTIONS") or {}).get("license_token")
    if not api_token or not license_token:
        raise DeezerError("Не удалось получить токены сессии Deezer")
    return api_token, license_token


def _track_token(session: requests.Session, api_token: str, sng_id: int) -> str:
    r = session.post(_GW, params={
        "method": "song.getData", "input": "3",
        "api_version": "1.0", "api_token": api_token,
    }, json={"sng_id": sng_id}, timeout=15)
    results = r.json().get("results") or {}
    token = results.get("TRACK_TOKEN")
    if not token:
        raise DeezerError("Не удалось получить TRACK_TOKEN (трек недоступен в регионе?)")
    return token


def _media_url(license_token: str, track_token: str) -> Tuple[str, str]:
    r = requests.post(_GET_URL, json={
        "license_token": license_token,
        "media": [{"type": "FULL", "formats": _FORMATS}],
        "track_tokens": [track_token],
    }, timeout=15)
    data = (r.json().get("data") or [{}])[0]
    if data.get("errors"):
        raise DeezerError(f"Deezer get_url: {data['errors']}")
    media = (data.get("media") or [])
    if not media:
        raise DeezerError("Deezer не вернул ссылку на медиа (формат не доступен аккаунту?)")
    fmt = media[0].get("format", "MP3_128")
    sources = media[0].get("sources") or []
    if not sources:
        raise DeezerError("Пустой список источников Deezer")
    return sources[0]["url"], fmt


def download_track(sng_id: int, arl: str, dest_dir: str) -> Tuple[str, str]:
    """Скачать и расшифровать трек Deezer по его id. Возвращает (path, format)."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    api_token, license_token = _gw_auth(session, arl)
    track_token = _track_token(session, api_token, sng_id)
    url, fmt = _media_url(license_token, track_token)

    os.makedirs(dest_dir, exist_ok=True)
    ext = _EXT.get(fmt, ".mp3")
    dest = os.path.join(dest_dir, f"deezer_{sng_id}{ext}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        _decrypt_to(resp, sng_id, dest)
    return dest, fmt


def download_by_isrc(isrc: str, arl: str, dest_dir: str) -> Tuple[str, str]:
    sng_id = resolve_isrc(isrc)
    if sng_id is None:
        raise DeezerError(f"Трек с ISRC {isrc} не найден в каталоге Deezer")
    return download_track(sng_id, arl, dest_dir)
