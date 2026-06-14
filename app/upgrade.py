"""Step 2: фоновый массовый апгрейд библиотеки до 320k через Spotify (librespot).

Проходит по всем трекам библиотеки (из *.spotdl в /conf), для каждого ещё не
апгрейженного скачивает 320k и перезаписывает файл по тому же пути. Метка «уже
320k» хранится персистентно (/conf/.webui-upgraded.json), поэтому повторно треки
НЕ качаются и прогресс переживает рестарт. Между загрузками — те же паузы
безопасного режима (jobs.spotify_throttle), отдельным потоком, не блокируя UI.
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time

from . import config

_STATE_FILE = os.path.join(config.CONF_DIR, ".webui-upgraded.json")
_DL_TIMEOUT = int(os.environ.get("ZOTIFY_TIMEOUT", "900"))


class _UpgradeController:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = False
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
            for t in data if isinstance(data, list) else []:
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
            if self.stats["state"] == "running":
                self.stats["state"] = "stopping"

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
            with self._lock:
                self.stats["current"] = item["name"]
            ok = self._download(url, item["name"])
            with self._lock:
                if ok:
                    self.upgraded.add(url)
                    self._save()
                    self.stats["done"] += 1
                else:
                    self.stats["failed"] += 1
        with self._lock:
            self.stats["state"] = "stopped" if self._stop else "idle"
            self.stats["current"] = ""

    def _download(self, url: str, name: str) -> bool:
        from . import jobs
        jobs.spotify_throttle(is_cancelled=lambda: self._stop)
        if self._stop:
            return False
        cmd = [sys.executable, "-m", "app.spotify_dl", url,
               config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT, config.UPLOAD_BITRATE]
        try:
            r = subprocess.run(cmd, cwd="/app", capture_output=True, text=True, timeout=_DL_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[upgrade] TIMEOUT {name}", flush=True)
            return False
        out = (r.stdout or "") + (r.stderr or "")
        print(f"[upgrade] {name}\n{out}", flush=True)  # виден в docker logs
        return r.returncode == 0 and "OK " in (r.stdout or "")


controller = _UpgradeController()
