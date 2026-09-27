"""Step 2: фоновый массовый апгрейд библиотеки до 320k через Spotify (librespot).

Проходит по всем трекам библиотеки (из *.spotdl в /conf), для каждого ещё не
апгрейженного скачивает 320k и перезаписывает файл по тому же пути. Метка «уже
320k» хранится персистентно (/conf/.webui-upgraded.json), поэтому повторно треки
НЕ качаются и прогресс переживает рестарт. Загрузки идут в real-time (скорость
прослушивания, анти-бан), отдельным потоком, не блокируя UI.
"""
import glob
import json
import os
import queue
import subprocess
import sys
import threading
import time

from . import config, navidrome, playlists, settings

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
        seen = {}
        for path in glob.glob(os.path.join(config.CONF_DIR, "*.spotdl")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            # раньше читался только формат-список (download), а `sync` пишет
            # объект {"songs": [...]} — треки синкнутых плейлистов апгрейд не видел
            for t in playlists.savefile_songs(data):
                url = t.get("url")
                if not url or url in seen:
                    continue
                name = f"{t.get('artist') or ''} - {t.get('name') or ''}".strip(" -")
                seen[url] = {"url": url, "name": name or url}
        return list(seen.values())

    # ---- статус для UI ----
    def status(self) -> dict:
        with self._lock:
            s = dict(self.stats)
        s["upgraded_total"] = len(self.upgraded)
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
            ok = self._download(url, item["name"])
            with self._lock:
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

    def _download(self, url: str, name: str) -> bool:
        if self._stop:
            return False
        try:
            w = self._ensure_worker()
            q = self._result_q
            w.stdin.write(url + "\n")
            w.stdin.flush()
        except Exception as exc:  # noqa: BLE001 — воркер умер при записи
            print(f"[upgrade] воркер недоступен ({exc}) — перезапуск", flush=True)
            self._kill_worker()
            return False
        try:
            line = q.get(timeout=settings.get("upgrade_track_timeout"))
        except queue.Empty:
            print(f"[upgrade] TIMEOUT {name} — перезапуск воркера", flush=True)
            self._kill_worker()
            return False
        if line is None:                   # воркер завершился
            print(f"[upgrade] воркер завершился на {name} — перезапуск", flush=True)
            self._kill_worker()
            return False
        try:
            res = json.loads(line)
        except (ValueError, TypeError):
            return False
        return bool(res.get("ok") or res.get("skipped"))


controller = _UpgradeController()
