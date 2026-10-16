"""Тест хода апгрейда (app/upgrade.py) с подставной загрузкой: счётчики,
список «Последние» (улучшен / уже хорошее / не вышло), остановка посреди
трека. Запуск: python tests/test_upgrade.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CONF_DIR"] = tempfile.mkdtemp(prefix="upgrade-")

from app import jobs, upgrade  # noqa: E402


def _controller(results):
    c = upgrade._UpgradeController()
    c.upgraded = {"https://open.spotify.com/track/already"}
    items = [{"url": u, "name": n} for u, n in (
        ("https://open.spotify.com/track/already", "Уже отмечен"),
        ("https://open.spotify.com/track/a", "Artist - Upgraded"),
        ("https://open.spotify.com/track/b", "Artist - Good already"),
        ("https://open.spotify.com/track/c", "Artist - Fails"),
    )]
    c._worklist = lambda: items
    c._download = lambda url, name, path="": results.get(url)
    c._kill_worker = lambda: None
    return c


def test_recent_and_counters():
    jobs.bulk_pause = lambda **kw: None          # без пауз в тесте
    c = _controller({"https://open.spotify.com/track/a": {"ok": True},
                     "https://open.spotify.com/track/b": {"ok": True, "skipped": True},
                     "https://open.spotify.com/track/c": {"ok": False, "error": "track unavailable"}})
    c._run()
    s = c.status()
    assert s["state"] == "idle" and s["done"] == 2 and s["failed"] == 1 and s["skipped"] == 1, s
    assert [r["result"] for r in s["recent"]] == ["failed", "good", "upgraded"], s["recent"]   # новые сверху
    assert s["recent"][0]["detail"] == "track unavailable"
    assert s["upgraded_total"] == 3                  # уже отмеченный + два новых


def test_stop_mid_track_is_not_a_failure():
    jobs.bulk_pause = lambda **kw: None
    c = _controller({})
    calls = []

    def download(url, name, path=""):
        calls.append(url)
        c._stop = True                               # остановили, пока трек качался
        return None
    c._download = download
    c._run()
    s = c.status()
    assert s["state"] == "stopped" and s["failed"] == 0 and s["recent"] == [], s
    assert len(calls) == 1


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
