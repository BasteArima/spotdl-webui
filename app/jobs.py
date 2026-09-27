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
import json
import os
import queue
import random
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from . import config, errors_parser, navidrome, playlists, settings
from .naming import is_saved

# ------------------------------------------------------------------ состояние
_jobs: Dict[str, "Job"] = {}
_jobs_order: List[str] = []
_jobs_lock = threading.Lock()
# Ревизия реестра: растёт при любом изменении состава/статусов задач. Нужна,
# чтобы /api/jobs отдавал ГОТОВЫЙ снимок, пока ничего не менялось: панель
# опрашивает раз в 1.5 с, а сборка снимка — три прохода по всему реестру.
_rev = 0
_rev_lock = threading.Lock()
_snapshot_cache = None            # (rev, limit, monotonic_ts, payload)
_SNAPSHOT_TTL = 5.0               # потолок на случай пропущенного bump'а
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
# Ретеншен завершённых задач — настройки «Сервис» (jobs_history, finished_log_lines).
# Раньше реестр рос бесконечно (удаление было только вручную из UI) — на пакетных
# загрузках это главная утечка памяти и лишняя работа на каждом опросе /api/jobs.
# У завершённой задачи интересен хвост лога с ошибкой, не тысячи строк.
_FINISHED = ("done", "error", "cancelled")

# Дорожки воркеров: интерактивная (ручные действия) и фоновая (тяжёлый автосинк).
LANE_INTERACTIVE = "interactive"
LANE_BACKGROUND = "background"
_LANE_BY_KIND = {
    "download": LANE_INTERACTIVE,
    "deezer": LANE_INTERACTIVE,      # фолбэк: скачать с Deezer по ISRC
    "zotify": LANE_INTERACTIVE,      # реальное аудио со Spotify (320k)
    "upload": LANE_INTERACTIVE,      # заливка локального файла + мета со Spotify
    "sync": LANE_INTERACTIVE,        # ручной sync одного плейлиста / обновление m3u
    "sync-all": LANE_BACKGROUND,     # «Синхронизировать всё» (тяжёлая)
    "autosync": LANE_BACKGROUND,     # плановый автосинк
}


class Job:
    def __init__(self, kind: str, title: str, runner: Callable[["Job"], None],
                 name: str = ""):
        self.id = _next_id()
        self.kind = kind            # download | sync | sync-all | autosync | retry
        self.lane = _LANE_BY_KIND.get(kind, LANE_INTERACTIVE)
        self.title = title          # полный заголовок (по нему группируем)
        self.name = name or title   # чистое имя плейлиста (без метода) для UI
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

    def trim_log(self, keep: int) -> None:
        """Подрезать лог до последних `keep` строк (для завершённых задач).
        Счётчик _dropped растёт, поэтому offset'ы дочитывания лога не ломаются."""
        with self._lock:
            overflow = len(self.log) - keep
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
                "name": self.name,
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


def _bump_rev() -> None:
    """Пометить реестр изменившимся — инвалидирует кэш снимка для /api/jobs."""
    global _rev
    with _rev_lock:
        _rev += 1


def _malloc_trim() -> None:
    """Вернуть освобождённую память аллокатора ядру ОС (glibc).

    Без этого RSS веб-процесса остаётся на пике после тяжёлого синка и висит
    так всё время простоя, хотя объекты уже собраны. На не-glibc — no-op."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 — не glibc / нет symbol'а: не критично
        pass


def _prune_finished_locked() -> int:
    """Вытеснить самые старые завершённые задачи сверх настройки jobs_history.

    Задачи из ещё АКТИВНЫХ групп (пакетная загрузка плейлиста, у которой что-то
    в очереди/идёт) не трогаем — иначе у группы поехал бы прогресс X/Y.
    Вызывать под _jobs_lock. Возвращает число вытесненных."""
    active_titles = {_jobs[jid].title for jid in _jobs_order
                     if _jobs[jid].status in ("queued", "running")}
    finished = [jid for jid in _jobs_order
                if _jobs[jid].status in _FINISHED
                and _jobs[jid].title not in active_titles]
    excess = len(finished) - settings.get("jobs_history")
    if excess <= 0:
        return 0
    drop = set(finished[:excess])
    for jid in drop:
        _jobs.pop(jid, None)
    _jobs_order[:] = [jid for jid in _jobs_order if jid not in drop]
    return len(drop)


# ------------------------------------------------------------------ локи
def _lock_file(playlist_id: str) -> str:
    return os.path.join(config.LOCKS_DIR, f"{playlist_id}.lock")


_LOCK_HEARTBEAT = 60     # владелец обновляет mtime лока раз в минуту
_LOCK_STALE = 300        # без обновления дольше 5 мин — владельца убили
_LOCK_WAIT = 600         # сколько ждать чужой лок


def clear_stale_locks() -> None:
    """Вызывается при старте сервиса: в этот момент ни один синк идти не может,
    значит, все локи в папке остались от убитого процесса (рестарт контейнера
    посреди синка). Локи ставит только этот сервис."""
    try:
        names = os.listdir(config.LOCKS_DIR)
    except OSError:
        return
    for name in names:
        if name.endswith(".lock"):
            try:
                os.remove(os.path.join(config.LOCKS_DIR, name))
            except OSError:
                pass


class PlaylistLock:
    """Файловый лок на время sync конкретного плейлиста.

    «Живой»: пока синк идёт, владелец раз в минуту обновляет mtime. Протухшим
    считается только лок без обновления дольше _LOCK_STALE (владельца убили),
    а не долгий честный синк — раньше порог был «30 мин от создания», и синк
    большого плейлиста (часы) через полчаса мог получить параллельного двойника."""

    def __init__(self, playlist_id: str, job: Job):
        self.path = _lock_file(playlist_id)
        self.job = job
        self.fd = None
        self._stop = threading.Event()

    def _heartbeat(self) -> None:
        while not self._stop.wait(_LOCK_HEARTBEAT):
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def __enter__(self):
        os.makedirs(config.LOCKS_DIR, exist_ok=True)
        deadline = time.time() + _LOCK_WAIT
        last_note = 0.0
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
                os.write(self.fd, str(os.getpid()).encode())
                threading.Thread(target=self._heartbeat, name=f"lock-{self.job.id}",
                                 daemon=True).start()
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > _LOCK_STALE:
                        os.remove(self.path)
                        self.job.append("[lock] снят брошенный лок (владелец не обновлял его > 5 мин)")
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"плейлист занят дольше {_LOCK_WAIT // 60} минут")
                if time.time() - last_note >= 60:      # не спамим лог каждые 3 с
                    self.job.append("[lock] плейлист занят, ждём освобождения...")
                    last_note = time.time()
                time.sleep(3)

    def __exit__(self, *exc):
        self._stop.set()
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


def _terminate_proc(proc) -> None:
    """Остановить подпроцесс И ВСЕХ его потомков (грандчайлдов ffmpeg/librespot):
    мягко (SIGTERM группе), через 5с — жёстко (SIGKILL группе). Иначе живой
    грандчайлд держит stdout-пайп открытым и цикл чтения в _run_process не получит
    EOF → очередь так и останется замороженной. На POSIX используем группу
    процессов (Popen(..., start_new_session=True)); на Windows (только локальные
    тесты) — одиночный terminate/kill, групп там нет."""
    posix = hasattr(os, "killpg")

    def _kill_group(sig) -> bool:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return True
        except Exception:  # noqa: BLE001 — процесс уже мёртв / нет прав
            return False

    if posix:
        _kill_group(signal.SIGTERM)
    else:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=5)
        return
    except Exception:  # noqa: BLE001 — TimeoutExpired или уже мёртв
        pass
    if posix:
        _kill_group(signal.SIGKILL)
    else:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# Значения этих флагов не пишем ни в лог задачи, ни в docker logs.
_SECRET_FLAGS = {"--client-secret"}


_TRACK_URL = "https://open.spotify.com/track/"


def _redact(cmd: List[str]) -> str:
    """Команда для лога: секреты — ***, длинный список ссылок на треки (Liked
    Songs, сотни штук) — одной пометкой вместо многокилобайтной строки."""
    tracks = sum(1 for p in cmd if p.startswith(_TRACK_URL))
    out, shown = [], False
    for i, part in enumerate(cmd):
        if i and cmd[i - 1] in _SECRET_FLAGS:
            out.append("***")
        elif tracks > 3 and part.startswith(_TRACK_URL):
            if not shown:
                out.append(f"<{tracks} ссылок на треки>")
                shown = True
        else:
            out.append(part)
    return " ".join(out)


def _run_process(job: Job, cmd: List[str], cwd: str = "/",
                 timeout: Optional[float] = None, kill_on_cancel: bool = True):
    """Запускает процесс, построчно пишет stdout/stderr в лог задачи И в stdout
    контейнера (видно в `docker logs`). Возвращает (код_возврата, строки_вывода).

    Сторож-поток убивает подпроцесс (и всю его группу), если:
      • он висит дольше `timeout` сек (зависшая librespot-сессия и т.п.), или
      • задачу отменили И kill_on_cancel=True.
    Без этого зависший подпроцесс заморозил бы чтение stdout → всю очередь.

    kill_on_cancel=False (sync): отмену НЕ форсируем убийством — текущий spotdl
    доработает и запишет m3u/save-file целиком, а sync-all остановится между
    плейлистами (мягко, без риска порчи файлов)."""
    job.append("$ " + _redact(cmd))
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    out_lines: List[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            # stdin закрыт: если подпроцесс вдруг попросит ввод (напр. spotipy
            # при протухшем OAuth-токене Liked Songs) — сразу EOF и ошибка,
            # а не вечное ожидание, блокирующее очередь.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
            start_new_session=True,   # своя группа процессов → можно убить ВСЕХ потомков
        )
    except FileNotFoundError:
        job.append(f"[error] не найден исполняемый файл: {cmd[0]}")
        job.returncode = 127
        return 127, out_lines

    deadline = (time.time() + timeout) if timeout else None
    watch = {"reason": None}

    def _watchdog() -> None:
        # пока процесс жив — следим за отменой/дедлайном; убиваем группу при нужде.
        # Убийство закрывает stdout → блокирующий цикл чтения ниже получит EOF.
        while proc.poll() is None:
            if kill_on_cancel and job.cancelled:
                watch["reason"] = "cancelled"
                _terminate_proc(proc)
                return
            if deadline is not None and time.time() > deadline:
                watch["reason"] = "timeout"
                _terminate_proc(proc)
                return
            time.sleep(1.0)

    # сторож нужен только если есть что сторожить (таймаут или жёсткая отмена)
    if deadline is not None or kill_on_cancel:
        threading.Thread(target=_watchdog, name=f"wd-{job.id}", daemon=True).start()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        out_lines.append(line)
        job.append(line)
        print(f"[{job.id}] {line}", flush=True)  # дублируем в docker logs
    proc.wait()

    if watch["reason"] == "timeout":
        job.append(f"[timeout] подпроцесс висел дольше {int(timeout)}с — убит; "
                   f"очередь продолжает работу")
    elif watch["reason"] == "cancelled":
        job.append("[cancelled] подпроцесс остановлен")
    job.returncode = proc.returncode
    job.append(f"[exit] код возврата: {proc.returncode}")
    return proc.returncode, out_lines


def run_spotdl(job: Job, args: List[str], timeout: Optional[float] = None,
               kill_on_cancel: bool = True):
    """Запуск spotdl с cwd=/ — критично: spotdl 4.5.0 прогоняет части пути --m3u
    через sanitize, который вырезает '/', превращая абсолютный путь в
    относительный. Из cwd=/ он резолвится обратно в /music/... (как автосинк).

    timeout задаём для одиночного скачивания трека (YouTube); для sync — None
    (синк целого плейлиста легитимно долгий) + kill_on_cancel=False (мягкая отмена,
    чтобы не оборвать запись m3u/save-file)."""
    return _run_process(job, [config.SPOTDL_BIN] + args, cwd="/",
                        timeout=timeout, kill_on_cancel=kill_on_cancel)


def _lyrics_args() -> List[str]:
    """--lyrics/--generate-lrc. .lrc требует провайдера `synced` — добавляем его
    первым, если пользователь включил .lrc. Пустой `--lyrics` = не искать тексты."""
    mode = settings.get("lyrics_providers")
    providers = [] if mode == "off" else mode.split(",")
    args = []
    if settings.get("generate_lrc"):
        if "synced" not in providers:
            providers.insert(0, "synced")
        args.append("--generate-lrc")
    return ["--lyrics", *providers] + args


def _common_output_args() -> List[str]:
    # Качество/поиск для YouTube раньше не передавались → spotdl брал свои
    # умолчания (в т.ч. mp3 128k). Теперь — из вкладки «Настройки».
    return [
        "--cookie-file", config.COOKIE_FILE,
        "--output", config.OUTPUT_TEMPLATE,
        "--format", config.AUDIO_FORMAT,
        "--bitrate", settings.get("youtube_bitrate"),
        "--audio", *settings.get("audio_providers").split(","),
        *_lyrics_args(),
        "--overwrite", settings.get("overwrite"),
        "--simple-tui",
        "--log-level", "INFO",
        "--threads", str(settings.get("spotdl_threads")),
    ]


LOGIN_HINT = "нет входа в Spotify — войдите во вкладке «Настройки» webui"


class LikedSongsUnavailable(RuntimeError):
    """Список Liked Songs не получить: нет входа, лимит Spotify и т.п."""


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _liked_songs_query(job: Job) -> List[str]:
    """Ссылки на треки Liked Songs — запросом к Spotify от имени пользователя
    (подпроцесс app.spotify_login --saved-urls, ~1 запрос на 50 треков).

    НЕ `spotdl sync saved --user-auth`: так spotdl гонит ВСЕ запросы через
    официальный API приложения пользователя и дозапрашивает каждый трек (~3
    запроса на трек) — на сотнях треков Spotify штрафует dev-приложение на
    сутки (429, Retry-After ~86400), а spotipy честно спит эти сутки внутри
    задачи. Список ссылок spotdl разбирает своим обычным клиентом, как плейлист."""
    if not config.spotify_user_logged_in():
        raise LikedSongsUnavailable(LOGIN_HINT)
    job.append("[liked] получаю список Liked Songs из Spotify…")
    try:
        r = subprocess.run([sys.executable, "-m", "app.spotify_login", "--saved-urls"],
                           cwd=_PROJECT_DIR, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=300)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        res = json.loads(lines[-1]) if lines else {}
    except subprocess.TimeoutExpired:
        raise LikedSongsUnavailable("Spotify не отдал список за 5 минут") from None
    except ValueError:
        res = {}
    if not res.get("ok"):
        err = res.get("error") or ("неожиданный ответ: " + (r.stderr or "").strip()[-300:])
        raise LikedSongsUnavailable(err + (" " + res["hint"] if res.get("hint") else ""))
    urls = res.get("urls") or []
    job.append(f"[liked] треков в Liked Songs: {len(urls)}")
    return urls


def sync_args(pl: playlists.Playlist, query: Optional[List[str]] = None) -> List[str]:
    """query — что синкать вместо pl.url (для Liked Songs — список ссылок на
    треки). Позиционные запросы идут ПЕРВЫМИ: флаги со списком значений
    (--audio, --lyrics) иначе съели бы их."""
    os.makedirs(config.ERRORS_DIR, exist_ok=True)
    os.makedirs(config.PLAYLISTS_M3U_DIR, exist_ok=True)
    return [
        "sync", *(query or [pl.url]),
        "--save-file", config.savefile_path(pl.id),
        "--save-errors", config.errors_path(pl.safe),
        "--m3u", config.m3u_path(pl.safe),
    ] + _common_output_args()


def download_match_args(youtube_url: str, spotify_url: str) -> List[str]:
    query = f"{youtube_url}|{spotify_url}"
    return ["download", query] + _common_output_args()


# ------------------------------------------------------------------ раннеры задач
def _reset_errors_file(safe: str) -> None:
    """Удаляет errors-файл плейлиста перед sync. spotdl пишет --save-errors в
    режиме ДОЗАПИСИ, поэтому без сброса повторные сканы копят дубли и устаревшие
    записи. После удаления spotdl создаёт файл заново со свежим полным списком."""
    p = config.errors_path(safe)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


# Какие плейлисты синкаются прямо сейчас (в этом процессе, обе дорожки). Второй
# sync того же плейлиста не ждёт лок часами, а сразу пропускается с причиной —
# типичный случай: ручной Sync, пока автосинк уже качает этот плейлист.
_active_syncs: Dict[str, str] = {}       # playlist_id -> job.id
_active_syncs_lock = threading.Lock()


class AlreadySyncing(RuntimeError):
    """Этот плейлист уже синкает другая задача."""


def _sync_locked(job: Job, pl: playlists.Playlist) -> None:
    """Sync плейлиста под локом со сбросом errors-файла (защита от дублей)."""
    with _active_syncs_lock:
        other = _active_syncs.get(pl.id)
        if other and other != job.id:
            other_job = get_job(other)
            where = f"«{other_job.title}»" if other_job else other
            raise AlreadySyncing(f"{pl.name}: уже синхронизируется в задаче {where} — "
                                 f"этот запуск не нужен, смотрите её лог")
        _active_syncs[pl.id] = job.id
    try:
        _sync_locked_inner(job, pl)
    finally:
        with _active_syncs_lock:
            if _active_syncs.get(pl.id) == job.id:
                del _active_syncs[pl.id]


def _sync_locked_inner(job: Job, pl: playlists.Playlist) -> None:
    query = None
    if is_saved(pl.url):
        # список — ДО сброса errors-файла: не получили список — старые
        # «ненайденные» остаются как были
        try:
            query = _liked_songs_query(job)
        except LikedSongsUnavailable as e:
            raise LikedSongsUnavailable(f"{pl.name}: {e}") from None
        if not query:
            job.append(f"[info] {pl.name}: Liked Songs пуст — нечего синхронизировать")
            return
    with PlaylistLock(pl.id, job):
        _reset_errors_file(pl.safe)
        # мягкая отмена: текущий spotdl допишет m3u/save-file, sync-all встанет
        # между плейлистами (а не порвёт файлы на полузаписи).
        run_spotdl(job, sync_args(pl, query), kill_on_cancel=False)


def _run_sync_playlist(job: Job, pl: playlists.Playlist) -> None:
    try:
        _sync_locked(job, pl)
    except AlreadySyncing as e:
        job.append(f"[skip] {e}")      # не ошибка: синк и так идёт


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
            _sync_locked(job, pl)
        except TimeoutError as e:
            job.append(f"[skip] {pl.name}: {e}")
        except (LikedSongsUnavailable, AlreadySyncing) as e:
            job.append(f"[skip] {e}")


def m3u_append_args(spotify_url: str, safe: str) -> List[str]:
    return [sys.executable, "-m", "app.append_m3u", spotify_url, safe]


def _download_track(job: Job, spotify_url: str, youtube_url: str, safe: str) -> bool:
    """Скачивает один трек (аудио с youtube_url, мета со spotify_url).
    При успехе убирает трек из errors-файла и ЛЕГКО дописывает его в m3u (без
    полного spotdl sync всего плейлиста). Возвращает True при успехе."""
    rc, out = run_spotdl(job, download_match_args(youtube_url, spotify_url),
                         timeout=settings.get("download_track_timeout"))
    if rc != 0 or looks_failed(out):
        job.status = "error"
        job.append("[error] загрузка не удалась — трек оставлен в списке ненайденных")
        return False
    if errors_parser.remove_track(safe, spotify_url):
        job.append("[ok] трек скачан, убран из errors-файла")
    else:
        job.append("[ok] трек скачан (в errors-файле не найден — возможно, уже убран)")
    # лёгкая дозапись ТОЛЬКО этого трека в m3u (вместо тяжёлого sync всего плейлиста)
    if safe:
        _run_process(job, m3u_append_args(spotify_url, safe), cwd="/app", timeout=180)
    return True


def _run_download_one(job: Job, spotify_url: str, youtube_url: str, safe: str) -> None:
    job.append("=== скачивание трека ===")
    _download_track(job, spotify_url, youtube_url, safe)


def deezer_dl_args(spotify_url: str, safe: str = "") -> List[str]:
    return [
        sys.executable, "-m", "app.deezer_dl",
        spotify_url, config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT, settings.get("upload_bitrate"), safe,
    ]


def _run_deezer(job: Job, spotify_url: str, safe: str) -> None:
    """Скачать трек с Deezer по ISRC (точное совпадение со Spotify) и положить в
    библиотеку. Для треков, которых нет на YouTube."""
    job.append("=== попытка скачать с Deezer (по ISRC) ===")
    rc, out = _run_process(job, deezer_dl_args(spotify_url, safe), cwd="/app",
                           timeout=settings.get("download_track_timeout"))
    ok = rc == 0 and any(line.startswith("OK ") for line in out)
    if not ok:
        job.status = "error"
        job.append("[error] Deezer не дал результата — трек оставлен в списке ненайденных")
        return
    if errors_parser.remove_track(safe, spotify_url):
        job.append("[ok] скачано с Deezer, трек убран из errors-файла")
    else:
        job.append("[ok] скачано с Deezer (в errors-файле не найдено — возможно, уже убрано)")


def spotify_dl_args(spotify_url: str, safe: str = "", realtime: bool = False,
                    min_bitrate: int = 0) -> List[str]:
    # min_bitrate>0 (пропуск уже-хороших файлов) задаёт ТОЛЬКО апгрейд; для ручной
    # кнопки он 0 → она всегда качает заново.
    return [
        sys.executable, "-m", "app.spotify_dl",
        spotify_url, config.OUTPUT_TEMPLATE, config.AUDIO_FORMAT, settings.get("upload_bitrate"),
        safe, "1" if realtime else "0", str(int(min_bitrate)),
    ]


def bulk_pause(is_cancelled=None, log=None) -> None:
    """Idle-пауза между треками (поверх real-time, анти-бан). Случайная в диапазоне
    ZOTIFY_BULK_WAIT_MIN..MAX. Прерывается отменой."""
    hi = settings.get("bulk_wait_max")
    if hi <= 0:
        return
    secs = random.uniform(min(settings.get("bulk_wait_min"), hi), hi)
    if log:
        log(f"[safe] пауза {int(secs)}с перед следующим треком")
    end = time.time() + secs
    while time.time() < end:
        if is_cancelled and is_cancelled():
            return
        time.sleep(min(1.0, max(0.0, end - time.time())))


def _run_zotify(job: Job, spotify_url: str, safe: str, force_realtime: bool = False) -> None:
    """Скачать трек напрямую со Spotify через librespot (320k с Premium) и
    положить в библиотеку. Real-time (скорость прослушивания, анти-бан): для
    массовой/апгрейд-загрузки всегда, для одиночной — при включённом без. режиме.
    Для массовой (force_realtime) при безопасном режиме — ещё пауза между треками."""
    realtime = force_realtime or settings.get_safe_mode()
    job.append(f"=== Spotify (librespot): скачивание{' [real-time]' if realtime else ''} ===")
    rc, out = _run_process(job, spotify_dl_args(spotify_url, safe, realtime), cwd="/app",
                           timeout=settings.get("download_track_timeout"))
    ok = rc == 0 and any(line.startswith("OK ") for line in out)
    if not ok:
        job.status = "error"
        job.append("[error] Spotify не дал результата — трек оставлен в списке ненайденных")
        return
    if errors_parser.remove_track(safe, spotify_url):
        job.append("[ok] скачано со Spotify, трек убран из errors-файла")
    else:
        job.append("[ok] скачано со Spotify (в errors не найдено — возможно, уже убрано)")
    # пауза между треками: только для массовой закачки и только при безопасном режиме
    # (после УСПЕХА — чтобы пачка падающих треков не простаивала впустую)
    if force_realtime and settings.get_safe_mode() and not job.cancelled:
        bulk_pause(lambda: job.cancelled, job.append)


def place_localfile_args(temp_path: str, spotify_url: str, safe: str = "") -> List[str]:
    return [
        sys.executable, "-m", "app.place_localfile",
        temp_path, spotify_url, config.OUTPUT_TEMPLATE,
        config.AUDIO_FORMAT, settings.get("upload_bitrate"), safe,
    ]


def _run_upload(job: Job, temp_path: str, spotify_url: str, safe: str, orig_name: str) -> None:
    """Заливка локального файла: ffmpeg-конвертация в формат библиотеки + мета и
    обложка со Spotify, размещение по тому же шаблону пути, что и обычная загрузка.
    То же, что spotdl делает после скачивания аудио — только источник локальный."""
    job.append(f"=== заливка локального файла: {orig_name} ===")
    rc, out = _run_process(job, place_localfile_args(temp_path, spotify_url, safe), cwd="/app",
                           timeout=settings.get("download_track_timeout"))
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


# ------------------------------------------------------------------ публичное API
def _enqueue(job: Job) -> Job:
    with _jobs_lock:
        _jobs[job.id] = job
        _jobs_order.append(job.id)
        _prune_finished_locked()
    _bump_rev()
    _queues[job.lane].put(job.id)
    return job


def enqueue_sync_playlist(url: str) -> Job:
    pl = playlists.find_by_url(url)
    if pl is None:
        raise ValueError("Плейлист не найден")
    job = Job("sync", f"Синхронизация: {pl.name}",
              lambda j: _run_sync_playlist(j, pl), name=pl.name)
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
              lambda j: _run_download_one(j, spotify_url, youtube_url, safe), name=safe)
    return _enqueue(job)


def enqueue_deezer(spotify_url: str, safe: str) -> Job:
    job = Job("deezer", f"Deezer: {safe}",
              lambda j: _run_deezer(j, spotify_url, safe), name=safe)
    return _enqueue(job)


def enqueue_zotify(spotify_url: str, safe: str, force_realtime: bool = False) -> Job:
    job = Job("zotify", f"Spotify 320k: {safe}",
              lambda j: _run_zotify(j, spotify_url, safe, force_realtime), name=safe)
    return _enqueue(job)


def enqueue_upload(temp_path: str, spotify_url: str, safe: str, orig_name: str) -> Job:
    job = Job("upload", f"Заливка файла ({safe})",
              lambda j: _run_upload(j, temp_path, spotify_url, safe, orig_name), name=safe)
    return _enqueue(job)


def enqueue_download_batch(items: List[dict]) -> dict:
    """Пакетная загрузка: на каждый трек — одна задача скачивания. m3u при этом
    дозаписывается ЛЕГКО внутри самой задачи (только скачанный трек), без отдельного
    тяжёлого `spotdl sync` всего плейлиста — раньше он сканировал сотни песен и
    блокировал очередь.

    items: [{spotify_url, youtube_url, safe}]
    Возвращает {downloads: [{id, spotify_url, safe}], syncs: []}.
    """
    downloads = []
    for it in items:
        sp = (it.get("spotify_url") or "").strip()
        yt = (it.get("youtube_url") or "").strip()
        safe = (it.get("safe") or "").strip()
        if not sp or not yt:
            continue
        job = enqueue_download_one(sp, yt, safe)
        downloads.append({"id": job.id, "spotify_url": sp, "safe": safe})
    return {"downloads": downloads, "syncs": []}


def get_job(job_id: str) -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(job_id)


def active_count() -> int:
    """Сколько задач реально активно (queued+running) по ВСЕМУ реестру —
    для счётчика в UI (выдача list_jobs ограничена лимитом и занижала бы число)."""
    with _jobs_lock:
        return sum(1 for j in _jobs.values() if j.status in ("queued", "running"))


def job_groups() -> List[dict]:
    """Агрегаты по заголовку для МНОГОэлементных групп (пакетная загрузка плейлиста):
    одна запись с прогрессом X/Y вместо кучи одинаковых строк. Считается по ВСЕМУ
    реестру (точные счётчики). Одиночные задачи (1 элемент) сюда НЕ попадают — они
    остаются обычными задачами в list_jobs. Сортировка — активные группы сверху."""
    with _jobs_lock:
        by_title: Dict[str, List[Job]] = {}
        for jid in _jobs_order:
            j = _jobs[jid]
            by_title.setdefault(j.title, []).append(j)
        groups: List[dict] = []
        for title, members in by_title.items():
            if len(members) < 2:
                continue
            cnt = {"queued": 0, "running": 0, "done": 0, "error": 0, "cancelled": 0}
            for m in members:
                cnt[m.status] = cnt.get(m.status, 0) + 1
            running, queued = cnt["running"], cnt["queued"]
            done, failed, cancelled = cnt["done"], cnt["error"], cnt["cancelled"]
            cur = next((m for m in members if m.status == "running"), None)
            if running or queued:
                status = "running" if running else "queued"
            elif failed and not done:
                status = "error"
            else:
                status = "done"
            groups.append({
                "group": True, "id": "grp:" + title, "title": title,
                "name": members[0].name, "kind": members[0].kind, "status": status,
                "total": len(members), "done": done, "failed": failed,
                "cancelled": cancelled, "queued": queued, "running": running,
                "processed": done + failed + cancelled,
                "current_id": cur.id if cur else None,
                "created": min(m.created for m in members),
                "finished": max((m.finished or 0.0) for m in members) or None,
            })
    groups.sort(key=lambda g: (
        0 if (g["running"] or g["queued"]) else 1,
        0 if g["running"] else 1,
        g["created"] if (g["running"] or g["queued"]) else -(g["finished"] or 0.0),
    ))
    return groups


def cancel_group(title: str) -> int:
    """Отменить все активные задачи группы (одного заголовка). Возвращает число отменённых."""
    with _jobs_lock:
        ids = [j.id for j in _jobs.values()
               if j.title == title and j.status in ("queued", "running")]
    return sum(1 for jid in ids if cancel_job(jid))


def remove_group(title: str) -> int:
    """Убрать все ЗАВЕРШЁННЫЕ задачи группы из списка. Возвращает число убранных."""
    with _jobs_lock:
        ids = [j.id for j in _jobs.values()
               if j.title == title and j.status in ("done", "error", "cancelled")]
    return sum(1 for jid in ids if remove_job(jid))


def remove_job(job_id: str) -> bool:
    """Убрать ЗАВЕРШЁННУЮ задачу из списка (done/error/cancelled). Активные
    (queued/running) удалять нельзя — их сначала надо отменить."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        if job.status in ("queued", "running"):
            return False
        _jobs.pop(job_id, None)
        try:
            _jobs_order.remove(job_id)
        except ValueError:
            pass
    _bump_rev()
    return True


def cancel_job(job_id: str) -> bool:
    """Отменить задачу. Только выставляем флаг (мгновенно, не блокируя запрос):
      • queued — воркер пропустит её при старте;
      • running per-track скачивание — сторож в _run_process убьёт подпроцесс и
        всю его группу в течение ~1с;
      • running sync — мягко: текущий spotdl допишет файлы, sync-all встанет
        между плейлистами.
    Файлы уже скачанных треков остаются; недокачанный — в «ненайденных»."""
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
    _bump_rev()
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
        # Порядок выдачи: running → недавно завершённые (новые первыми) → голова
        # очереди. КРИТИЧНО при большой очереди: бегущая задача обычно в НАЧАЛЕ
        # order (низкий id), а прежний `order[-limit:]` отдавал ХВОСТ (новейшие
        # queued) и вытеснял её — в UI всё выглядело как «в очереди», ни одной
        # «идёт», и завершённые треки из начала не убирались.
        running = [jid for jid in order if _jobs[jid].status == "running"]
        finished = [jid for jid in reversed(order)
                    if _jobs[jid].status in ("done", "error", "cancelled")]
        queued = [jid for jid in order if _jobs[jid].status == "queued"]
        ids = (running + finished + queued)[:limit]
        out = []
        for i in ids:
            d = _jobs[i].to_dict()
            if d["status"] == "queued":
                d["queue_pos"] = pos.get(i)
            out.append(d)
        return out


def snapshot(limit: int = 50) -> dict:
    """Готовый ответ для /api/jobs, закэшированный по ревизии реестра.

    Панель опрашивается раз в 1.5 с (и из каждой открытой вкладки), а сборка —
    три прохода по всему реестру. Пока состав/статусы не менялись, отдаём тот
    же объект; TTL — страховка на случай пропущенного _bump_rev()."""
    global _snapshot_cache
    cached = _snapshot_cache
    now = time.monotonic()
    if (cached is not None and cached[0] == _rev and cached[1] == limit
            and now - cached[2] < _SNAPSHOT_TTL):
        return cached[3]
    rev_at_start = _rev
    payload = {
        "jobs": list_jobs(limit),
        "active": active_count(),
        "groups": job_groups(),
    }
    _snapshot_cache = (rev_at_start, limit, now, payload)
    return payload


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
        _bump_rev()
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
            if job.status == "done":
                navidrome.notify_changed()   # скан — позже, когда очередь затихнет
            # Завершённой задаче полный лог больше не нужен: оставляем хвост,
            # чистим вытесненные задачи и отдаём память ОС — иначе сервис
            # держит пик потребления всё время простоя.
            job.trim_log(settings.get("finished_log_lines"))
            with _jobs_lock:
                _prune_finished_locked()
            _bump_rev()
            idle = all(q2.empty() for q2 in _queues.values())
            if idle:
                _malloc_trim()
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
_SCHED_TICK = 60.0            # как часто планировщик сверяется с настройками
_last_autosync = time.time()  # отсчёт интервала — от старта или последнего автосинка


def _autosync_now() -> None:
    global _last_autosync
    _last_autosync = time.time()
    if not _has_pending_sync_all():
        enqueue_sync_all(auto=True)


def next_autosync() -> Optional[float]:
    """Когда будет следующий автосинк (unix-время) или None, если выключен."""
    hours = settings.get("autosync_interval_hours")
    return _last_autosync + hours * 3600 if hours > 0 else None


def _scheduler() -> None:
    """Фоновый автосинк — замена отдельного контейнера-автосинка. Все загрузки
    проходят через тот же воркер и те же локи, что и ручные операции.

    Интервал берётся из настроек на КАЖДОМ шаге, поэтому смена во вкладке
    «Настройки» действует сразу, без рестарта. Шаг — минута: в простое это
    ничего не стоит."""
    global _last_autosync
    # «при старте» — только если автосинк вообще включён (интервал 0 = выключен совсем)
    if settings.get("autosync_on_start") and settings.get("autosync_interval_hours") > 0:
        time.sleep(max(0, settings.get("autosync_start_delay")))
        _autosync_now()
    while True:
        time.sleep(_SCHED_TICK)
        try:
            navidrome.maybe_scan(busy=active_count() > 0)
        except Exception as exc:  # noqa: BLE001 — планировщик не должен умирать
            print(f"[navidrome] ошибка авто-скана: {exc}", flush=True)
        hours = settings.get("autosync_interval_hours")
        if hours <= 0:
            # выключен: отсчёт «замораживаем», чтобы при включении интервал
            # начался заново, а не сработал мгновенно за всё время простоя
            _last_autosync = time.time()
            continue
        if time.time() - _last_autosync >= hours * 3600:
            _autosync_now()


_scheduler_thread: Optional[threading.Thread] = None


def start_scheduler() -> None:
    """Запускает планировщик всегда: включение/выключение и интервал — в настройках."""
    global _scheduler_thread
    if _scheduler_thread is None or not _scheduler_thread.is_alive():
        _scheduler_thread = threading.Thread(target=_scheduler, name="spotdl-scheduler", daemon=True)
        _scheduler_thread.start()
