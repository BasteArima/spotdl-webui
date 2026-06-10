"""Очередь задач и запуск spotdl.

Все тяжёлые операции (sync / download) сериализуются через один воркер-поток,
поэтому внутри сервиса параллельных загрузок не бывает. Для координации с
ФОНОВЫМ автосинком используется файловый лок на плейлист (best-effort: автосинк
локов не ставит, но это снижает вероятность одновременного sync одного URL).

Команды spotdl строятся ровно с теми же флагами/шаблонами, что у автосинка:
    sync URL --save-file /conf/<id>.spotdl --cookie-file /conf/cookies.txt \
             --save-errors /conf/errors/<safe>.txt \
             --output "<OUTPUT_TEMPLATE>" --m3u /music/playlists/<safe>.m3u8 \
             --format mp3
    download "<youtube>|<spotify>" --cookie-file ... --output "<...>" --format mp3
"""
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from . import config, errors_parser, playlists

# ------------------------------------------------------------------ состояние
_jobs: Dict[str, "Job"] = {}
_jobs_order: List[str] = []
_jobs_lock = threading.Lock()
# Две независимые дорожки: интерактивная (ручные действия идут сразу) и фоновая
# (тяжёлый автосинк). Один и тот же плейлист защищён файловым локом, поэтому
# дорожки не подерутся за один sync.
_queues: Dict[str, "queue.Queue[str]"] = {
    "interactive": queue.Queue(),
    "background": queue.Queue(),
}
_seq = 0
_seq_lock = threading.Lock()


def _next_id() -> str:
    global _seq
    with _seq_lock:
        _seq += 1
        return f"job{_seq:05d}"


MAX_LOG_LINES = 4000  # держим в памяти только хвост лога каждой задачи

# Дорожки воркеров: интерактивная (ручные действия) и фоновая (тяжёлый автосинк).
LANE_INTERACTIVE = "interactive"
LANE_BACKGROUND = "background"
_LANE_BY_KIND = {
    "download": LANE_INTERACTIVE,
    "upload": LANE_INTERACTIVE,      # заливка локального файла + мета со Spotify
    "sync": LANE_INTERACTIVE,        # ручной sync одного плейлиста / обновление m3u
    "retry": LANE_INTERACTIVE,
    "sync-all": LANE_BACKGROUND,     # «Синхронизировать всё» (тяжёлая)
    "autosync": LANE_BACKGROUND,     # плановый автосинк
}


class Job:
    def __init__(self, kind: str, title: str, runner: Callable[["Job"], None]):
        self.id = _next_id()
        self.kind = kind            # download | sync | sync-all | autosync | retry
        self.lane = _LANE_BY_KIND.get(kind, LANE_INTERACTIVE)
        self.title = title
        self.runner = runner
        self.status = "queued"      # queued | running | done | error
        self.log: List[str] = []
        self._dropped = 0           # сколько строк лога вытеснено из начала (для offset)
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.returncode: Optional[int] = None
        self.cancelled = False
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        with self._lock:
            for line in text.splitlines():
                self.log.append(line)
            overflow = len(self.log) - MAX_LOG_LINES
            if overflow > 0:
                del self.log[:overflow]
                self._dropped += overflow

    def total_log_len(self) -> int:
        return self._dropped + len(self.log)

    def to_dict(self, include_log: bool = False, since: int = 0) -> dict:
        with self._lock:
            d = {
                "id": self.id,
                "kind": self.kind,
                "lane": self.lane,
                "title": self.title,
                "status": self.status,
                "created": self.created,
                "started": self.started,
                "finished": self.finished,
                "returncode": self.returncode,
                "log_len": self.total_log_len(),
            }
            if include_log:
                abs_start = max(since, self._dropped)   # вытесненные строки пропускаем
                local = abs_start - self._dropped
                d["log"] = self.log[local:]
                d["log_offset"] = abs_start
        return d


# ------------------------------------------------------------------ локи
def _lock_file(playlist_id: str) -> str:
    return os.path.join(config.LOCKS_DIR, f"{playlist_id}.lock")


class PlaylistLock:
    """Простой файловый лок. Захватывается на время sync конкретного плейлиста."""

    def __init__(self, playlist_id: str, job: Job):
        self.path = _lock_file(playlist_id)
        self.job = job
        self.fd = None

    def __enter__(self):
        os.makedirs(config.LOCKS_DIR, exist_ok=True)
        # ждём освобождения до 10 минут
        deadline = time.time() + 600
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                # лок занят — проверим, не протух ли (старше 30 мин)
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > 1800:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"Лок {self.path} занят дольше 10 минут")
                self.job.append("[lock] плейлист занят, ждём освобождения...")
                time.sleep(3)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            os.remove(self.path)
        except OSError:
            pass


# ------------------------------------------------------------------ запуск spotdl
# Маркеры провала в выводе spotdl. spotdl 4.5.0 умеет завершаться с кодом 0,
# даже когда аудио не скачалось (например AudioProviderError), поэтому одного
# кода возврата мало — дополнительно смотрим на вывод.
_FAIL_MARKERS = (
    "AudioProviderError",
    "LookupError",
    "YT-DLP download error",
    "Some YouTube downloads require Deno",
    "DownloadError",
    "No results found",
)


def looks_failed(lines: List[str]) -> bool:
    return any(any(m in ln for m in _FAIL_MARKERS) for ln in lines)


def _run_process(job: Job, cmd: List[str], cwd: str = "/"):
    """Запускает процесс, построчно пишет stdout/stderr в лог задачи И в stdout
    контейнера (видно в `docker logs`). Возвращает (код_возврата, строки_вывода)."""
    job.append("$ " + " ".join(cmd))
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    out_lines: List[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        job.append(f"[error] не найден исполняемый файл: {cmd[0]}")
        job.returncode = 127
        return 127, out_lines
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        out_lines.append(line)
        job.append(line)
        print(f"[{job.id}] {line}", flush=True)  # дублируем в docker logs
    proc.wait()
    job.returncode = proc.returncode
    job.append(f"[exit] код возврата: {proc.returncode}")
    return proc.returncode, out_lines


def run_spotdl(job: Job, args: List[str]):
    """Запуск spotdl с cwd=/ — критично: spotdl 4.5.0 прогоняет части пути --m3u
    через sanitize, который вырезает '/', превращая абсолютный путь в
    относительный. Из cwd=/ он резолвится обратно в /music/... (как автосинк)."""
    return _run_process(job, [config.SPOTDL_BIN] + args, cwd="/")


def _common_output_args() -> List[str]:
    return [
        "--cookie-file", config.COOKIE_FILE,
        "--output", config.OUTPUT_TEMPLATE,
        "--format", config.AUDIO_FORMAT,
        "--simple-tui",
        "--log-level", "INFO",
    ]


def sync_args(pl: playlists.Playlist) -> List[str]:
    os.makedirs(config.ERRORS_DIR, exist_ok=True)
    os.makedirs(config.PLAYLISTS_M3U_DIR, exist_ok=True)
    return [
        "sync", pl.url,
        "--save-file", config.savefile_path(pl.id),
        "--save-errors", config.errors_path(pl.safe),
        "--m3u", config.m3u_path(pl.safe),
    ] + _common_output_args()


def download_match_args(youtube_url: str, spotify_url: str) -> List[str]:
    query = f"{youtube_url}|{spotify_url}"
    return ["download", query] + _common_output_args()


def retry_errors_args(spotify_urls: List[str]) -> List[str]:
    """Расширенный авто-повтор: пробуем несколько источников аудио для треков.

    ВАЖНО: spotdl распознаёт как файл-список только `*.spotdl`. Путь к errors/*.txt
    он бы воспринял как поисковый запрос, поэтому передаём СПИСОК Spotify-URL
    отдельными аргументами (каждый open.spotify.com/track/... обрабатывается)."""
    return [
        "download", *spotify_urls,
        "--audio", "youtube-music", "youtube", "soundcloud",
    ] + _common_output_args()


# ------------------------------------------------------------------ раннеры задач
def _run_sync_playlist(job: Job, pl: playlists.Playlist) -> None:
    with PlaylistLock(pl.id, job):
        run_spotdl(job, sync_args(pl))


def _run_sync_all(job: Job) -> None:
    pls = playlists.read_playlists()
    if not pls:
        job.append("[info] playlists.txt пуст — нечего синхронизировать")
        return
    for pl in pls:
        if job.cancelled:
            job.append("[cancelled] остановлено пользователем")
            return
        job.append(f"=== sync: {pl.name} ===")
        try:
            with PlaylistLock(pl.id, job):
                run_spotdl(job, sync_args(pl))
        except TimeoutError as e:
            job.append(f"[skip] {pl.name}: {e}")


def _download_track(job: Job, spotify_url: str, youtube_url: str, safe: str) -> bool:
    """Скачивает один трек (аудио с youtube_url, мета со spotify_url).
    При успехе убирает трек из errors-файла. Возвращает True при успехе.
    НЕ запускает sync — обновление m3u делается отдельной задачей (одной на
    плейлист), чтобы пакетная загрузка не плодила лишние синки."""
    rc, out = run_spotdl(job, download_match_args(youtube_url, spotify_url))
    if rc != 0 or looks_failed(out):
        job.status = "error"
        job.append("[error] загрузка не удалась — трек оставлен в списке ненайденных")
        return False
    if errors_parser.remove_track(safe, spotify_url):
        job.append("[ok] трек скачан, убран из errors-файла")
    else:
        job.append("[ok] трек скачан (в errors-файле не найден — возможно, уже убран)")
    return True


def _run_download_one(job: Job, spotify_url: str, youtube_url: str, safe: str) -> None:
    job.append("=== скачивание трека ===")
    _download_track(job, spotify_url, youtube_url, safe)


def place_localfile_args(temp_path: str, spotify_url: str) -> List[str]:
    return [
        sys.executable, "-m", "app.place_localfile",
        temp_path, spotify_url, config.OUTPUT_TEMPLATE,
        config.AUDIO_FORMAT, config.UPLOAD_BITRATE,
    ]


def _run_upload(job: Job, temp_path: str, spotify_url: str, safe: str, orig_name: str) -> None:
    """Заливка локального файла: ffmpeg-конвертация в формат библиотеки + мета и
    обложка со Spotify, размещение по тому же шаблону пути, что и обычная загрузка.
    То же, что spotdl делает после скачивания аудио — только источник локальный."""
    job.append(f"=== заливка локального файла: {orig_name} ===")
    rc, out = _run_process(job, place_localfile_args(temp_path, spotify_url), cwd="/app")
    try:
        os.remove(temp_path)
    except OSError:
        pass
    ok = rc == 0 and any(line.startswith("OK ") for line in out)
    if not ok:
        job.status = "error"
        job.append("[error] заливка не удалась — трек оставлен в списке ненайденных")
        return
    if errors_parser.remove_track(safe, spotify_url):
        job.append("[ok] файл размещён, трек убран из errors-файла")
    else:
        job.append("[ok] файл размещён (в errors-файле не найден — возможно, уже убран)")


def _run_sync_for_m3u(job: Job, safe: str) -> None:
    """Обновляет m3u плейлиста через sync (чтобы скачанные треки попали в Navidrome)."""
    pl = playlists.find_by_safe(safe)
    if pl is None:
        job.append(f"[warn] плейлист для '{safe}' не найден в playlists.txt — m3u не обновлён")
        return
    job.append(f"=== sync '{pl.name}' для обновления m3u ===")
    try:
        with PlaylistLock(pl.id, job):
            run_spotdl(job, sync_args(pl))
    except TimeoutError as e:
        job.append(f"[warn] не удалось взять лок для sync: {e}")


def _run_retry(job: Job, safe: str) -> None:
    job.append(f"=== расширенный повторный поиск по errors/{safe}.txt ===")
    data = errors_parser.parse_errors_file(config.errors_path(safe))
    urls = [t["spotify_url"] for t in data.get("tracks", []) if t.get("spotify_url")]
    if not urls:
        job.append("[info] в errors-файле нет треков для повтора")
        return
    job.append(f"[info] пробуем {len(urls)} трек(ов) через youtube-music / youtube / soundcloud")
    pl = playlists.find_by_safe(safe)
    if pl is not None:
        with PlaylistLock(pl.id, job):
            run_spotdl(job, retry_errors_args(urls))
        job.append(f"=== sync '{pl.name}' для обновления m3u ===")
        with PlaylistLock(pl.id, job):
            run_spotdl(job, sync_args(pl))
    else:
        run_spotdl(job, retry_errors_args(urls))


# ------------------------------------------------------------------ публичное API
def _enqueue(job: Job) -> Job:
    with _jobs_lock:
        _jobs[job.id] = job
        _jobs_order.append(job.id)
    _queues[job.lane].put(job.id)
    return job


def enqueue_sync_playlist(url: str) -> Job:
    pl = playlists.find_by_url(url)
    if pl is None:
        raise ValueError("Плейлист не найден")
    job = Job("sync", f"Синхронизация: {pl.name}",
              lambda j: _run_sync_playlist(j, pl))
    return _enqueue(job)


def enqueue_sync_all(auto: bool = False) -> Job:
    title = "Автосинхронизация (по расписанию)" if auto else "Синхронизация всех плейлистов"
    kind = "autosync" if auto else "sync-all"
    job = Job(kind, title, _run_sync_all)
    return _enqueue(job)


def _has_pending_sync_all() -> bool:
    """Есть ли уже незавершённый sync-всех/автосинк — чтобы не плодить дубли."""
    with _jobs_lock:
        for jid in _jobs_order:
            j = _jobs[jid]
            if j.kind in ("sync-all", "autosync") and j.status in ("queued", "running"):
                return True
    return False


def enqueue_download_one(spotify_url: str, youtube_url: str, safe: str) -> Job:
    job = Job("download", f"Скачивание трека ({safe})",
              lambda j: _run_download_one(j, spotify_url, youtube_url, safe))
    return _enqueue(job)


def enqueue_upload(temp_path: str, spotify_url: str, safe: str, orig_name: str) -> Job:
    job = Job("upload", f"Заливка файла ({safe})",
              lambda j: _run_upload(j, temp_path, spotify_url, safe, orig_name))
    return _enqueue(job)


def enqueue_sync_for_m3u(safe: str) -> Job:
    job = Job("sync", f"Обновление m3u: {safe}",
              lambda j: _run_sync_for_m3u(j, safe))
    return _enqueue(job)


def enqueue_download_batch(items: List[dict]) -> dict:
    """Пакетная загрузка: на каждый трек — задача скачивания, затем ОДНА задача
    sync на каждый затронутый плейлист (а не на каждый трек). Один воркер +
    FIFO гарантируют, что sync пойдёт после всех загрузок этого плейлиста.

    items: [{spotify_url, youtube_url, safe}]
    Возвращает {downloads: [{id, spotify_url, safe}], syncs: [{id, safe}]}.
    """
    downloads = []
    safes_order: List[str] = []
    for it in items:
        sp = (it.get("spotify_url") or "").strip()
        yt = (it.get("youtube_url") or "").strip()
        safe = (it.get("safe") or "").strip()
        if not sp or not yt:
            continue
        job = enqueue_download_one(sp, yt, safe)
        downloads.append({"id": job.id, "spotify_url": sp, "safe": safe})
        if safe and safe not in safes_order:
            safes_order.append(safe)
    syncs = []
    for safe in safes_order:
        job = enqueue_sync_for_m3u(safe)
        syncs.append({"id": job.id, "safe": safe})
    return {"downloads": downloads, "syncs": syncs}


def enqueue_retry(safe: str) -> Job:
    job = Job("retry", f"Повторный поиск: {safe}",
              lambda j: _run_retry(j, safe))
    return _enqueue(job)


def get_job(job_id: str) -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> bool:
    """Отменить задачу. Для queued — снимется до запуска (воркер пропустит).
    Для running — мягкий запрос: длинные циклы (sync-всех) прервутся между
    плейлистами, но текущий шаг spotdl доработает."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        if job.status in ("done", "error", "cancelled"):
            return False
        job.cancelled = True
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
    return True


def list_jobs(limit: int = 50) -> List[dict]:
    with _jobs_lock:
        # позиция в очереди: сколько queued/running впереди в той же дорожке
        order = list(_jobs_order)
        ahead = {"interactive": 0, "background": 0}
        pos = {}
        for jid in order:
            j = _jobs[jid]
            if j.status == "queued":
                ahead[j.lane] += 1
                pos[jid] = ahead[j.lane]
            elif j.status == "running":
                ahead[j.lane] += 1
        ids = order[-limit:][::-1]
        out = []
        for i in ids:
            d = _jobs[i].to_dict()
            if d["status"] == "queued":
                d["queue_pos"] = pos.get(i)
            out.append(d)
        return out


# ------------------------------------------------------------------ воркеры (по дорожке)
def _worker(lane: str) -> None:
    q = _queues[lane]
    while True:
        job_id = q.get()
        job = get_job(job_id)
        if job is None:
            q.task_done()
            continue
        if job.cancelled:           # отменена ещё в очереди — пропускаем
            if job.status not in ("cancelled",):
                job.status = "cancelled"
                job.finished = time.time()
            q.task_done()
            continue
        job.status = "running"
        job.started = time.time()
        try:
            job.runner(job)
            if job.cancelled:
                job.status = "cancelled"
            elif job.status != "error":
                job.status = "done"
        except Exception as e:  # noqa: BLE001 — любая ошибка должна попасть в лог
            job.status = "error"
            job.append(f"[exception] {type(e).__name__}: {e}")
        finally:
            job.finished = time.time()
            q.task_done()


_worker_threads: Dict[str, threading.Thread] = {}


def start_worker() -> None:
    for lane in _queues:
        t = _worker_threads.get(lane)
        if t is None or not t.is_alive():
            t = threading.Thread(target=_worker, args=(lane,),
                                 name=f"spotdl-worker-{lane}", daemon=True)
            t.start()
            _worker_threads[lane] = t


# ------------------------------------------------------------------ планировщик
def _scheduler() -> None:
    """Фоновый автосинк — замена отдельного контейнера-автосинка.
    Раз в AUTOSYNC_INTERVAL_HOURS ставит в очередь sync-всех. Все загрузки
    проходят через тот же воркер и те же локи, что и ручные операции."""
    interval = config.AUTOSYNC_INTERVAL_HOURS * 3600.0
    if config.AUTOSYNC_ON_START:
        time.sleep(max(0.0, config.AUTOSYNC_START_DELAY))
        if not _has_pending_sync_all():
            enqueue_sync_all(auto=True)
    while True:
        time.sleep(interval)
        if not _has_pending_sync_all():
            enqueue_sync_all(auto=True)


_scheduler_thread: Optional[threading.Thread] = None


def start_scheduler() -> None:
    """Запускает планировщик, если интервал > 0."""
    global _scheduler_thread
    if config.AUTOSYNC_INTERVAL_HOURS <= 0:
        return
    if _scheduler_thread is None or not _scheduler_thread.is_alive():
        _scheduler_thread = threading.Thread(target=_scheduler, name="spotdl-scheduler", daemon=True)
        _scheduler_thread.start()
