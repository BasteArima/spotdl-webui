"""Тесты соответствия safe-имени, id и парсинга эталонному поведению bash/sed
автосинка. Запуск:  python -m pytest tests/ -q  (или python tests/test_naming.py)"""
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.naming import playlist_id, safe_name, url_type  # noqa: E402
from app import errors_parser, config  # noqa: E402


def test_safe_name_basic():
    # двоеточие -> пробел, схлопывание, как в ТЗ
    assert safe_name("NieR: Automata OST") == "NieR Automata OST"


def test_safe_name_all_bad_chars():
    assert safe_name(r'a/b\c:d*e?f"g<h>i|j') == "a b c d e f g h i j"


def test_safe_name_collapse_and_trim():
    assert safe_name("foo   bar   ") == "foo bar"
    assert safe_name('weird:  :name  ') == "weird name"


def test_safe_name_unicode_preserved():
    # кириллица и японский не трогаются
    assert safe_name("封印 - Seal the Light") == "封印 - Seal the Light"
    assert safe_name("Концентрат") == "Концентрат"


def test_safe_name_matches_sed_reference():
    """Сверка с фактическим выводом эталонного sed-конвейера (если есть sh/sed)."""
    import shutil
    import subprocess

    sed = shutil.which("sed")
    sh = shutil.which("sh")
    if not sed or not sh:
        return  # на Windows без sed просто пропускаем
    samples = [
        "NieR: Automata OST",
        'Tricky "Name": part/2',
        "封印 - Seal the Light",
        "Concentrate",
        "a*b?c<d>e|f",
    ]
    # Скармливаем образец через stdin как UTF-8 байты и читаем вывод как UTF-8 —
    # иначе на Windows argv/locale (cp1251) портит не-ASCII символы.
    script = r"""sed 's#[/\\:*?"<>|]# #g; s/  */ /g; s/[[:space:]]*$//'"""
    for s in samples:
        out = subprocess.run([sh, "-c", script], input=s.encode("utf-8"),
                             capture_output=True)
        expected = out.stdout.decode("utf-8").rstrip("\n")
        assert safe_name(s) == expected, f"{s!r}: py={safe_name(s)!r} sed={expected!r}"


def test_playlist_id_matches_md5sum():
    url = "https://open.spotify.com/playlist/3pQsB6Scx6L1Ru7RrVjxiS"
    expected = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    assert playlist_id(url) == expected
    assert len(playlist_id(url)) == 12


def test_url_type():
    assert url_type("https://open.spotify.com/playlist/3pQsB6Scx6L1Ru7RrVjxiS") == "playlist"
    assert url_type("https://open.spotify.com/album/3WFTgbz04SxliMLzfQappc") == "album"
    assert url_type("https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb") == "track"
    assert url_type("https://open.spotify.com/artist/abc") == "artist"
    assert url_type("https://example.com/foo") == "unknown"


SAMPLE_ERRORS = """2026-06-09-10-09-33
https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb - LookupError: No results found for song: Arcane - Vow (feat. Ray Chen)
https://open.spotify.com/track/6MyDghp72szyzsZT2hlY6w - LookupError: No results found for song: Fenton Rider - Evil Morty Sad Theme (Orchestral Version)
"""


def test_parse_errors():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "NieR Automata OST.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE_ERRORS)
        data = errors_parser.parse_errors_file(path)
        assert data["safe"] == "NieR Automata OST"
        assert data["timestamp"] == "2026-06-09-10-09-33"
        assert len(data["tracks"]) == 2
        t0 = data["tracks"][0]
        assert t0["spotify_url"] == "https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb"
        assert t0["error_type"] == "LookupError"
        assert t0["song"] == "Arcane - Vow (feat. Ray Chen)"


DUP_ERRORS = """2026-06-01-10-00-00
https://open.spotify.com/track/t1 - LookupError: No results found for song: A - One
https://open.spotify.com/track/t2 - LookupError: No results found for song: B - Two
2026-06-02-12-30-00
https://open.spotify.com/track/t1 - LookupError: No results found for song: A - One
https://open.spotify.com/track/t2 - LookupError: No results found for song: B - Two
https://open.spotify.com/track/t3 - LookupError: No results found for song: C - Three
"""


def test_parse_errors_dedup():
    """spotdl дозаписывает --save-errors при каждом скане → дубли; парсер их
    схлопывает по Spotify-URL и берёт последний таймстамп."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "P.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(DUP_ERRORS)
        data = errors_parser.parse_errors_file(path)
        urls = [t["spotify_url"].rsplit("/", 1)[-1] for t in data["tracks"]]
        assert urls == ["t1", "t2", "t3"], urls           # 3 уникальных, не 5
        assert data["timestamp"] == "2026-06-02-12-30-00"  # последний таймстамп


def test_remove_track(monkeypatch=None):
    with tempfile.TemporaryDirectory() as d:
        errdir = os.path.join(d, "errors")
        os.makedirs(errdir)
        path = os.path.join(errdir, "NieR Automata OST.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE_ERRORS)
        old = config.ERRORS_DIR
        config.ERRORS_DIR = errdir
        try:
            ok = errors_parser.remove_track(
                "NieR Automata OST",
                "https://open.spotify.com/track/15xVX45khwdkd5RACVBAgb",
            )
            assert ok
            data = errors_parser.parse_errors_file(path)
            assert len(data["tracks"]) == 1
            assert data["tracks"][0]["spotify_url"].endswith("6MyDghp72szyzsZT2hlY6w")
        finally:
            config.ERRORS_DIR = old


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # чтобы print не падал на не-ASCII
    except Exception:  # noqa: BLE001
        pass
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failures else 0)
