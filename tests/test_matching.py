"""Тесты сопоставления (app/matching.py) на примерах из реальных логов синка.
Запуск: python tests/test_matching.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.matching import artist_key, artists_overlap, compare, parse_title, title_key  # noqa: E402


def T(title, artists, duration=200, isrc=None, **ids):
    return {"title": title, "artists": artists, "duration": duration, "isrc": isrc, "ids": ids}


# ---------------------------------------------------------------- разбор названия
def test_noise_and_feat_removed():
    assert parse_title("Hurt (Official Video)") == ("hurt", ())
    assert parse_title("Hurt [Official Music Video]") == ("hurt", ())
    assert parse_title("Seven (feat. Latto) (Explicit Ver.)") == ("seven", ())
    assert parse_title("Seven feat. Latto") == ("seven", ())
    assert parse_title("Enemy (with JID) - from the series Arcane League of Legends") == ("enemy", ())
    assert parse_title("Enemy - from the series Arcane League of Legends") == ("enemy", ())
    assert parse_title('Ragnarök (From "God of War Ragnarök" Soundtrack)') == ("ragnarök", ())
    assert parse_title("Spoiler - Original Mix") == ("spoiler", ())
    assert parse_title("Rock It (Lyrics)") == ("rock it", ())


def test_version_markers_kept():
    assert parse_title("Hurt - Live") == ("hurt", ("live",))
    assert parse_title("Iris - Remastered 2023") == ("iris", ("remaster",))
    assert parse_title("Iris (2004 Remaster)") == ("iris", ("remaster",))
    assert parse_title("Lagtrain - Russian ver.") == ("lagtrain", ("russian version",))
    assert parse_title("Golden (Russian Cover)") == ("golden", ("russian cover",))
    assert parse_title("Little Dark Age - Slowed") == ("little dark age", ("slowed",))
    assert parse_title("Cyberpunk 2077 Kerrys Song - TFM Remix") == ("cyberpunk 2077 kerrys song", ("tfm remix",))
    assert parse_title("Weight of the World - English Version") == ("weight of the world", ("english version",))
    assert parse_title("On The Way (TV-size)") == ("on the way", ("tv size",))
    assert parse_title("Self Love - Spider-Man: Across the Spider-Verse - Instrumental")[1] == ("instrumental",)


def test_scripts_kept_as_is():
    assert parse_title("Охота на лисицу") == ("охота на лисицу", ())
    assert parse_title("ラグトレイン") == ("ラグトレイン", ())
    assert parse_title("Weight of the World/壊レタ世界ノ歌")[0] == "weight of the world 壊レタ世界ノ歌"
    assert parse_title("ｗｅｉｇｈｔ") == ("weight", ())          # полноширинные → обычные


def test_title_key():
    assert title_key("Hurt (Official Video)") == title_key("Hurt") == "hurt|"
    assert title_key("Hurt - Live") == "hurt|live"
    assert title_key("Iris - Remastered 2023") != title_key("Iris")


# ---------------------------------------------------------------- исполнители
def test_artist_keys():
    assert artist_key("Johnny Cash - Topic") == artist_key("Johnny Cash") == "johnnycash"
    assert artist_key("JohnnyCashVEVO") == "johnnycash"
    assert artist_key("The Goo Goo Dolls") == artist_key("Goo Goo Dolls")
    assert artist_key("HUNTR/X") == "huntrx"
    assert artists_overlap(["AC", "DC"], ["AC/DC"])            # spotdl разрезал «/»
    assert artists_overlap(["Imagine Dragons", "JID"], ["JID", "Arcane"])
    assert not artists_overlap(["Lofis"], ["MGMT"])
    assert not artists_overlap([], ["MGMT"])


# ---------------------------------------------------------------- сравнение
def test_level1_same_video_or_track():
    a = T("Enemy (with JID) - from the series Arcane League of Legends", ["Imagine Dragons", "JID"], youtube="F5tSoaJ93ac")
    b = T("Enemy - from the series Arcane League of Legends", ["Imagine Dragons", "JID", "Arcane"], youtube="F5tSoaJ93ac")
    assert compare(a, b)[0] == 1
    assert compare(T("x", ["a"], spotify="S1"), T("y", ["b"], spotify="S1"))[0] == 1


def test_level2_isrc():
    a = T("Hurt", ["Johnny Cash"], 218, isrc="USUM70209713")
    b = T("Hurt", ["Johnny Cash"], 218, isrc="USUM70209713")
    assert compare(a, b)[:2] == (2, 99)


def test_level3_same_title_artist_duration():
    a = T("Hurt", ["Johnny Cash"], 218, isrc="USUM70209713")
    b = T("Hurt (Official Video)", ["Johnny Cash - Topic"], 219.5)
    assert compare(a, b)[0] == 3
    iris = T("Iris", ["The Goo Goo Dolls"], 289, isrc="USWB19800123")
    iris_single = T("Iris", ["The Goo Goo Dolls"], 290, isrc="USWB19800999")   # перевыпуск: ISRC другой
    assert compare(iris, iris_single)[0] == 3
    assert compare(T("Seven (feat. Latto) (Explicit Ver.)", ["Jung Kook", "Latto"], 184),
                   T("Seven", ["Jung Kook"], 185))[0] == 3


def test_versions_are_different_tracks():
    hurt = T("Hurt", ["Johnny Cash"], 218)
    assert compare(hurt, T("Hurt - Live", ["Johnny Cash"], 251)) is None
    assert compare(T("Iris", ["The Goo Goo Dolls"], 289), T("Iris - Remastered 2023", ["The Goo Goo Dolls"], 289)) is None
    assert compare(T("ラグトレイン", ["稲葉曇"], 246), T("Lagtrain - Russian ver.", ["Sati Akura"], 247)) is None
    assert compare(T("Golden", ["HUNTR/X"], 194), T("Golden (Russian Cover)", ["TRISHA"], 196)) is None
    assert compare(T("Little Dark Age", ["MGMT"], 300), T("Little Dark Age - Slowed", ["Lofis"], 330)) is None
    assert compare(T("Weight of the World/壊レタ世界ノ歌", ["Keiichi Okabe"], 341),
                   T("Weight of the World - English Version", ["Keiichi Okabe"], 338)) is None
    # live против live — одна версия, но разные концерты не различаем: длительность решает
    assert compare(T("Way Down We Go", ["KALEO"], 219), T("Way Down We Go - Live", ["KALEO"], 251)) is None


def test_level4_needs_decision():
    # длительность отличается на 3–10 с — спросить
    r = compare(T("Hurt", ["Johnny Cash"], 218), T("Hurt", ["Johnny Cash"], 226))
    assert r[0] == 4 and "8 с" in r[2], r
    # исполнитель на YouTube другой (канал-перезалив), название и длительность те же
    r = compare(T("Affection", ["Jinsang"], 177), T("Affection", ["beats4you"], 178))
    assert r[0] == 4 and r[1] == 70, r
    # опечатка / пунктуация
    r = compare(T("Any Special Orders? (Nico's Shop)", ["Steven Mcnair"], 120),
                T("Any Special Order (Nicos Shop)", ["Steven McNair"], 121))
    assert r and r[0] == 4, r


def test_different_songs_not_matched():
    assert compare(T("Golden", ["HUNTR/X"], 194), T("Golden Hour", ["JVKE"], 209)) is None
    assert compare(T("Intro", ["The xx"], 127), T("Intro", ["M83"], 330)) is None
    assert compare(T("Hurt", ["Johnny Cash"], 218), T("Hurt", ["Nine Inch Nails"], 373)) is None


if __name__ == "__main__":
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
                import traceback
                traceback.print_exc()
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failures else 0)
