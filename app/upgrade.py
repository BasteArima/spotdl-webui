"""Step 2: фоновый массовый апгрейд библиотеки до 320k через Spotify (librespot).

Проходит по всем файлам библиотеки с треком Spotify (индекс /conf/library.db),
для каждого ещё не апгрейженного скачивает 320k и перезаписывает ИМЕННО этот
файл (путь из индекса, а не по шаблону: трек мог прийти под другим релизом или
остаться после склейки дублей — иначе рядом появился бы второй файл). Метка «уже
320k» хранится персистентно (/conf/.webui-upgraded.json), поэтому повторно треки
НЕ качаются и прогресс переживает рестарт. Загрузки идут в real-time (скорость
прослушивания, анти-бан), отдельным потоком, не блокируя UI.
"""
import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Optional

from . import config, navidrome, settings

_STATE_FILE = os.path.join(config.CONF_DIR, ".webui-upgraded.json")


class _UpgradeController:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = False
        self._day = ""
        self._day_count = 0
        # Долгоживущий воркер: одна авторизация librespot на весь сеанс
        self._worker = None
        self._result_q = None
        self.upgraded = self._load()
        # последние результаты — для раздела «Качество 320k» (только в памяти)
        self.recent = collections.deque(maxlen=20)
        self.stats = {
            "state": "idle",          # idle | running | stopping | stopped
            "total": 0, "done": 0, "failed": 0, "skipped": 0,
            "current": "", "started": None,
        }

    # ---- персистентные метки «уже 320k» ----
    def _load(self) -> set:
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as fh:
                return set(json.load(fh))
        except (OSError, ValueError):
            return set()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_STATE_FILE) or ".", exist_ok=True)
            tmp = _STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(sorted(self.upgraded), fh)
            os.replace(tmp, _STATE_FILE)
        except OSError:
            pass

    # ---- список треков библиотеки ----
    def _worklist(self) -> list:
        """Файлы библиотеки с треком Spotify. У склеенного трека ID бывает
        несколько — уже апгрейженный под любым из них не качается снова."""
        from . import db
        out = []
        with db.connect() as conn:
            for r in conn.execute(
                    "SELECT t.path, t.artist, t.title, GROUP_CONCAT(i.value) AS ids FROM tracks t "
                    "JOIN track_ids i ON i.track_id = t.id AND i.kind = 'spotify' "
                    "WHERE t.status = 'ok' AND t.path IS NOT NULL GROUP BY t.id ORDER BY t.id"):
                urls = [f"https://open.spotify.com/track/{v}" for v in sorted((r["ids"] or "").split(",")) if v]
                if not urls:
                    continue
                done = next((u for u in urls if u in self.upgraded), None)
                name = f"{r['artist'] or ''} - {r['title'] or ''}".strip(" -")
                out.append({"url": done or urls[0], "name": name or urls[0], "path": r["path"]})
        return out

    # ---- статус для UI ----
    def status(self) -> dict:
        with self._lock:
            s = dict(self.stats)
        s["upgraded_total"] = len(self.upgraded)
        s["recent"] = list(self.recent)
        processed = s["done"] + s["failed"]
        if s["state"] == "running" and processed > 0 and s["started"]:
            elapsed = time.time() - s["started"]
            rate = processed / elapsed
            remaining = max(0, s["total"] - s["done"] - s["failed"] - s["skipped"])
            s["eta_seconds"] = int(remaining / rate) if rate > 0 else None
        else:
            s["eta_seconds"] = None
        return s

    # ---- управление ----
    def start(self) -> bool:
        with self._lock:
            if self.stats["state"] == "running":
                return False
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="upgrade", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop = True
        with self._lock:
            if self.stats["state"] in ("running", "waiting"):
                self.stats["state"] = "stopping"
        self._kill_worker()

    # ---- основной цикл ----
    def _run(self) -> None:
        wl = self._worklist()
        with self._lock:
            self.stats.update(state="running", total=len(wl), done=0,
                              failed=0, skipped=0, current="", started=time.time())
        for item in wl:
            if self._stop:
                break
            url = item["url"]
            if url in self.upgraded:
                with self._lock:
                    self.stats["skipped"] += 1
                continue
            if not self._daily_gate():
                break
            with self._lock:
                self.stats["current"] = item["name"]
            res = self._download(url, item["name"], item.get("path") or "")
            if res is None and self._stop:
                break                        # остановили посреди трека — это не сбой трека
            ok = bool(res and (res.get("ok") or res.get("skipped")))
            with self._lock:
                self.recent.appendleft({
                    "name": item["name"], "ts": time.time(),
                    # skipped — файл уже не ниже порога (upgrade_min_bitrate), качать не стали
                    "result": "failed" if not ok else ("good" if res.get("skipped") else "upgraded"),
                    "detail": str((res or {}).get("error") or "")[:200],
                })
                if ok:
                    navidrome.notify_changed()
                    self.upgraded.add(url)
                    self._save()
                    self.stats["done"] += 1
                    self._day_count += 1
                else:
                    self.stats["failed"] += 1
            # idle-пауза между треками (поверх real-time, анти-бан)
            if not self._stop:
                from . import jobs
                jobs.bulk_pause(is_cancelled=lambda: self._stop)
        self._kill_worker()
        with self._lock:
            self.stats["state"] = "stopped" if self._stop else "idle"
            self.stats["current"] = ""

    def _daily_gate(self) -> bool:
        """Соблюсти лимит загрузок в сутки. Возвращает False, если остановлено."""
        limit = settings.get("upgrade_per_day")
        if limit <= 0:
            return True
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day = today
            self._day_count = 0
        if self._day_count < limit:
            return True
        # лимит исчерпан — ждём наступления следующих суток (UTC)
        with self._lock:
            self.stats["state"] = "waiting"
            self.stats["current"] = f"дневной лимит {limit} достигнут — пауза до след. суток"
        while not self._stop and time.strftime("%Y-%m-%d", time.gmtime()) == today:
            time.sleep(30)
        if self._stop:
            return False
        self._day = time.strftime("%Y-%m-%d", time.gmtime())
        self._day_count = 0
        with self._lock:
            self.stats["state"] = "running"
        return True

    # ---- долгоживущий воркер (одна авторизация librespot на весь сеанс) ----
    def _spawn_worker(self):
        # realtime="1", min_bitrate из config — фиксированы на весь сеанс воркера.
        cmd = [sys.executable, "-m", "app.spotify_worker",
               config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT, settings.get("upload_bitrate"),
               "1", str(settings.get("upgrade_min_bitrate"))]
        w = subprocess.Popen(cmd, cwd="/app", stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        q = queue.Queue()

        def _reader():
            for line in w.stdout:          # одна JSON-строка результата на трек
                q.put(line)
            q.put(None)                    # EOF/смерть воркера

        def _stderr_drain():
            for line in w.stderr:          # диагностика → docker logs
                print(f"[upgrade] {line.rstrip()}", flush=True)

        threading.Thread(target=_reader, daemon=True).start()
        threading.Thread(target=_stderr_drain, daemon=True).start()
        self._worker, self._result_q = w, q
        return w

    def _ensure_worker(self):
        if self._worker is None or self._worker.poll() is not None:
            return self._spawn_worker()
        return self._worker

    def _kill_worker(self):
        w = self._worker
        self._worker, self._result_q = None, None
        if w is None:
            return
        try:
            if w.stdin:
                w.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            w.terminate()
        except Exception:  # noqa: BLE001
            pass

    def _download(self, url: str, name: str, path: str = "") -> Optional[dict]:
        """Ответ воркера по треку ({"ok"|"skipped"|"error"…}) или None при сбое."""
        if self._stop:
            return None
        try:
            w = self._ensure_worker()
            q = self._result_q
            w.stdin.write(url + ("\t" + path if path else "") + "\n")
            w.stdin.flush()
        except Exception as exc:  # noqa: BLE001 — воркер умер при записи
            print(f"[upgrade] воркер недоступен ({exc}) — перезапуск", flush=True)
            self._kill_worker()
            return None
        try:
            line = q.get(timeout=settings.get("upgrade_track_timeout"))
        except queue.Empty:
            print(f"[upgrade] TIMEOUT {name} — перезапуск воркера", flush=True)
            self._kill_worker()
            return None
        if line is None:                   # воркер завершился
            print(f"[upgrade] воркер завершился на {name} — перезапуск", flush=True)
            self._kill_worker()
            return None
        try:
            res = json.loads(line)
        except (ValueError, TypeError):
            return None
        return bool(res.get("ok") or res.get("skipped"))


controller = _UpgradeController()
