"""Тестовая библиотека для локального стенда (docker-compose.dev.yml) и для
отладки сопоставления/склейки дублей (design/plan.md, этап 1).

Создаёт ./_dev/music (короткие mp3 с тегами — тем же embed_metadata, что у
spotdl) и ./_dev/conf (playlists.txt, save-файлы, errors). Случаи взяты из
реальных логов синка: один трек в двух релизах Spotify, один ролик YouTube
на два трека Spotify, «(Official Video)», live/ремастер/кавер (это НЕ дубли),
кириллица и японский, не найденные на YouTube.

Запуск на машине разработчика (нужны ffmpeg и spotdl):
    python tests/make_dev_library.py [--out _dev]
Существующие _dev/music и _dev/conf пересоздаются.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "_dev"
MUSIC, CONF = os.path.join(OUT, "music"), os.path.join(OUT, "conf")

_n = [0]


def sid(tag: str) -> str:
    """Стабильный «ID Spotify» из 22 символов."""
    import hashlib
    return hashlib.md5(tag.encode()).hexdigest()[:22]


# (ключ, название, исполнители, альбом, альбом-исполнитель, длит., ISRC, ролик YouTube)
T = [
    # обычные треки
    ("hurt", "Hurt", ["Johnny Cash"], "American IV: The Man Comes Around", "Johnny Cash", 218, "USUM70209713", "8AHCfZTRGiI"),
    ("seven", "Seven (feat. Latto) (Explicit Ver.)", ["Jung Kook", "Latto"], "Seven", "Jung Kook", 184, "USA2P2312345", "QU9c0053UAU"),
    ("iris", "Iris", ["The Goo Goo Dolls"], "Dizzy up the Girl", "The Goo Goo Dolls", 289, "USWB19800123", "NdYWuo9OFAw"),
    ("enemy", "Enemy (with JID) - from the series Arcane League of Legends", ["Imagine Dragons", "JID"],
     "Arcane League of Legends", "Arcane", 173, "USUM72118123", "F5tSoaJ93ac"),
    ("golden", "Golden", ["HUNTR/X", "EJAE", "AUDREY NUNA"], "KPop Demon Hunters", "HUNTR/X", 194, "USA2P2525423", "yebNIHKAC4A"),
    ("okhota", "Охота на лисицу", ["Green Apelsin"], "Охота на лисицу", "Green Apelsin", 201, "RUA1D2200001", "wUOGOMNNI_Q"),
    ("lagtrain", "ラグトレイン", ["稲葉曇"], "ラグトレイン", "稲葉曇", 246, "JPV752000123", "VfBswbj1824"),
    ("wotw_jp", "Weight of the World/壊レタ世界ノ歌", ["Keiichi Okabe"], "NieR:Automata OST", "Keiichi Okabe", 341, "JPS111700012", "aaaaaaaaaa1"),
    ("wotw_en", "Weight of the World - English Version", ["Keiichi Okabe", "J'Nique Nicole"], "NieR:Automata OST",
     "Keiichi Okabe", 338, "JPS111700013", "aaaaaaaaaa2"),
    # тот же трек вторым релизом: другой ID Spotify, тот же ISRC (уровень 2)
    ("hurt_comp", "Hurt", ["Johnny Cash"], "The Essential Johnny Cash", "Johnny Cash", 218, "USUM70209713", "8AHCfZTRGiI"),
    # тот же трек, ISRC другой (перевыпуск), название/исполнитель/длительность совпадают (уровень 3)
    ("iris_single", "Iris", ["The Goo Goo Dolls"], "Iris (Single)", "The Goo Goo Dolls", 290, "USWB19800999", "zzzzzzzzzz1"),
    # spotdl взял ОДИН ролик для двух разных треков Spotify (уровень 1 — звук одинаковый)
    ("enemy_arcane", "Enemy - from the series Arcane League of Legends", ["Imagine Dragons", "JID", "Arcane"],
     "Enemy (from Arcane)", "Imagine Dragons", 173, "USUM72118999", "F5tSoaJ93ac"),
    # НЕ дубли: live, ремастер, кавер, русская версия, slowed
    ("hurt_live", "Hurt - Live", ["Johnny Cash"], "Live at Montreux", "Johnny Cash", 251, "USSM10300001", "live0000001"),
    ("iris_remaster", "Iris - Remastered 2023", ["The Goo Goo Dolls"], "Dizzy up the Girl (Remastered)", "The Goo Goo Dolls",
     289, "USWB12300001", "remast00001"),
    ("lagtrain_ru", "Lagtrain - Russian ver.", ["Sati Akura"], "Lagtrain", "Sati Akura", 247, "RUB422100001", "Llj7cvOhNhg"),
    ("golden_cover", "Golden (Russian Cover)", ["TRISHA"], "Golden", "TRISHA", 196, "RUC012500001", "TtlyqSF2AfU"),
    ("dark_slowed", "Little Dark Age - Slowed", ["Lofis"], "Slowed", "Lofis", 330, "USL112300001", "slowed00001"),
    ("dark", "Little Dark Age", ["MGMT"], "Little Dark Age", "MGMT", 300, "USSM11706190", "rtL5oMyBHPs"),
]
TRACKS = {t[0]: t for t in T}

# файлы есть у всех, кроме этих (их «не нашли» / «YouTube не отдал» / без объяснений)
NO_FILE = {"seven": ("LookupError", "No results found for song: Jung Kook - Seven (feat. Latto) (Explicit Ver.)"),
           "lagtrain_ru": ("AudioProviderError", "YT-DLP download error - https://music.youtube.com/watch?v=Llj7cvOhNhg"),
           "dark_slowed": (None, None)}

PLAYLISTS = [
    ("Любимые", "saved", ["hurt", "seven", "iris", "golden", "okhota", "lagtrain", "wotw_en", "dark", "dark_slowed"]),
    ("Для работы", "https://open.spotify.com/playlist/37i9dQZF1DX0000000000001",
     ["hurt_comp", "iris_single", "enemy", "wotw_jp", "hurt_live", "iris_remaster"]),
    ("Arcane", "https://open.spotify.com/album/0jV32pIk8l3vTaU3thTaW2", ["enemy", "enemy_arcane", "golden_cover"]),
    ("Каверы", "https://open.spotify.com/playlist/37i9dQZF1DX0000000000002", ["lagtrain_ru", "golden_cover", "lagtrain"]),
]


def song(key: str):
    from spotdl.types.song import Song
    k, name, artists, album, aa, dur, isrc, yt = TRACKS[key]
    return Song.from_missing_data(
        name=name, artists=artists, artist=artists[0], album_name=album, album_artist=aa, album_id=sid(album),
        genres=[], disc_number=1, disc_count=1, track_number=1 + sum(map(ord, key)) % 12, tracks_count=12,
        year=2020, date="2020-01-01", song_id=sid(key), url=f"https://open.spotify.com/track/{sid(key)}",
        isrc=isrc, duration=dur, explicit=False, publisher="", cover_url=None, copyright_text=None, lyrics=None,
        download_url=f"https://music.youtube.com/watch?v={yt}")


def make_file(key: str) -> str:
    from spotdl.utils.formatter import create_file_name
    from spotdl.utils.metadata import embed_metadata
    s = song(key)
    rel = create_file_name(s, "{album-artist}/{album}/{track-number} - {title}.{output-ext}", "mp3")
    path = Path(MUSIC) / "spotify" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # тишина настоящей длины (сопоставление опирается на длительность звука), но
    # в минимальном качестве — 8 кбит/с моно, ~300 КБ на пятиминутный трек
    dur = TRACKS[key][5]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                    "-t", str(dur), "-c:a", "libmp3lame", "-b:a", "8k", str(path)], check=True)
    embed_metadata(path, s, skip_album_art=True)
    return str(path)


def main() -> None:
    from app.naming import playlist_id, safe_name
    for d in (MUSIC, CONF):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(CONF, "errors"))
    os.makedirs(os.path.join(MUSIC, "playlists"))
    made = [make_file(k) for k in TRACKS if k not in NO_FILE]
    # трек, загруженный вручную, без тегов площадок
    manual = os.path.join(MUSIC, "upload", "Dustin O'Halloran - Opus 23.mp3")
    os.makedirs(os.path.dirname(manual))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "2", "-c:a", "libmp3lame", manual], check=True)

    with open(os.path.join(CONF, "playlists.txt"), "w", encoding="utf-8") as fh:
        fh.write("# тестовая библиотека: tests/make_dev_library.py\n")
        for name, url, _keys in PLAYLISTS:
            fh.write(f"{name}|{url}\n")
    for name, url, keys in PLAYLISTS:
        songs = [song(k).json for k in keys]
        data = songs if url == "saved" else {"type": "sync", "query": [url], "songs": songs}
        with open(os.path.join(CONF, f"{playlist_id(url)}.spotdl"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        errs = [(k, NO_FILE[k]) for k in keys if k in NO_FILE and NO_FILE[k][0]]
        if errs:
            with open(os.path.join(CONF, "errors", f"{safe_name(name)}.txt"), "w", encoding="utf-8") as fh:
                fh.write("2026-10-15-04-00-00\n")
                for k, (etype, text) in errs:
                    fh.write(f"https://open.spotify.com/track/{sid(k)} - {etype}: {text}\n")
    print(f"готово: {len(made) + 1} файлов в {MUSIC}, {len(PLAYLISTS)} плейлиста в {CONF}")


if __name__ == "__main__":
    main()
