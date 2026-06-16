"""FastAPI-приложение: API для управления плейлистами, просмотра ненайденных
треков и добивания. Аутентификация — токен в заголовке (Bearer / X-Auth-Token).
Токен НИКОГДА не передаётся в URL/query."""
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, errors_parser, health, jobs, playlists, settings, upgrade

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="spotdl web-ui", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup() -> None:
    if not config.APP_AUTH_TOKEN:
        # Жёсткий отказ стартовать без токена — сервис умеет запускать процессы.
        raise RuntimeError(
            "APP_AUTH_TOKEN не задан. Установите переменную окружения с непустым "
            "токеном, иначе сервис стартовать не будет."
        )
    jobs.start_worker()
    jobs.start_scheduler()  # фоновый автосинк по расписанию (если включён)


# ------------------------------------------------------------------ авторизация
def _extract_token(authorization: Optional[str], x_auth_token: Optional[str]) -> str:
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if x_auth_token:
        return x_auth_token.strip()
    return ""


def require_auth(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token"),
) -> None:
    token = _extract_token(authorization, x_auth_token)
    expected = config.APP_AUTH_TOKEN
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Требуется авторизация")


# ------------------------------------------------------------------ модели тела
class PlaylistIn(BaseModel):
    name: str
    url: str


class PlaylistEdit(BaseModel):
    old_url: str
    name: str
    url: str


class PlaylistRef(BaseModel):
    url: str


class DownloadItem(BaseModel):
    spotify_url: str
    youtube_url: str
    safe: str


class DownloadBatchIn(BaseModel):
    items: list[DownloadItem]


# ------------------------------------------------------------------ статика / здоровье
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ------------------------------------------------------------------ playlists
@app.get("/api/playlists", dependencies=[Depends(require_auth)])
def api_list_playlists():
    return {"playlists": [p.to_dict() for p in playlists.read_playlists()]}


@app.post("/api/playlists", dependencies=[Depends(require_auth)])
def api_add_playlist(body: PlaylistIn):
    try:
        p = playlists.add_playlist(body.name, body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"playlist": p.to_dict()}


@app.put("/api/playlists", dependencies=[Depends(require_auth)])
def api_edit_playlist(body: PlaylistEdit):
    try:
        p = playlists.update_playlist(body.old_url, body.name, body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"playlist": p.to_dict()}


@app.delete("/api/playlists", dependencies=[Depends(require_auth)])
def api_delete_playlist(body: PlaylistRef):
    if not playlists.delete_playlist(body.url):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"ok": True}


# ------------------------------------------------------------------ errors
@app.get("/api/errors", dependencies=[Depends(require_auth)])
def api_list_errors():
    return {"groups": errors_parser.list_errors()}


_NAME_CACHE: dict = {}


class ResolveNamesIn(BaseModel):
    urls: list[str]


@app.post("/api/resolve-names", dependencies=[Depends(require_auth)])
def api_resolve_names(body: ResolveNamesIn):
    """Дорезолвить настоящие имена треков со Spotify (для битых имён вроде
    'musicShelfRenderer'). Результат кешируется, резолв — в подпроцессе."""
    todo = [u for u in body.urls if u and u not in _NAME_CACHE][:60]
    if todo:
        try:
            r = subprocess.run([sys.executable, "-m", "app.resolve_names", *todo],
                               cwd="/app", capture_output=True, text=True, timeout=90)
            # берём последнюю непустую строку stdout (json), чтобы случайный вывод
            # при импорте spotdl не сломал разбор
            lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
            data = json.loads(lines[-1]) if lines else {}
            if isinstance(data, dict):
                _NAME_CACHE.update(data)
        except Exception:  # noqa: BLE001
            pass
    return {u: _NAME_CACHE[u] for u in body.urls if _NAME_CACHE.get(u)}


# ------------------------------------------------------------------ задачи
@app.post("/api/sync", dependencies=[Depends(require_auth)])
def api_sync(body: PlaylistRef):
    try:
        job = jobs.enqueue_sync_playlist(body.url)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"job": job.to_dict()}


@app.post("/api/sync-all", dependencies=[Depends(require_auth)])
def api_sync_all():
    job = jobs.enqueue_sync_all()
    return {"job": job.to_dict()}


@app.post("/api/download-batch", dependencies=[Depends(require_auth)])
def api_download_batch(body: DownloadBatchIn):
    items = []
    for it in body.items:
        if it.youtube_url.strip() and it.spotify_url.strip():
            items.append({
                "spotify_url": it.spotify_url.strip(),
                "youtube_url": it.youtube_url.strip(),
                "safe": it.safe.strip(),
            })
    if not items:
        raise HTTPException(status_code=400, detail="Нет треков с заполненным источником")
    result = jobs.enqueue_download_batch(items)
    return result


_SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/track/", re.IGNORECASE)


class DeezerIn(BaseModel):
    spotify_url: str
    safe: str


@app.post("/api/deezer", dependencies=[Depends(require_auth)])
def api_deezer(body: DeezerIn):
    """Скачать трек с Deezer по ISRC (фолбэк для отсутствующих на YouTube)."""
    sp = body.spotify_url.strip()
    safe = body.safe.strip()
    if not _SPOTIFY_TRACK_RE.search(sp):
        raise HTTPException(status_code=400, detail="Нужна ссылка на трек Spotify (open.spotify.com/track/…)")
    if not config.deezer_arl():
        raise HTTPException(status_code=400, detail="Deezer ARL не настроен (DEEZER_ARL или /conf/deezer_arl.txt)")
    # sync на обновление m3u ставит сама задача — только при успехе, без дублей
    job = jobs.enqueue_deezer(sp, safe)
    return {"job": job.to_dict()}


class ZotifyIn(BaseModel):
    spotify_url: str
    safe: str
    bulk: bool = False   # массовая закачка → всегда real-time


@app.post("/api/zotify", dependencies=[Depends(require_auth)])
def api_zotify(body: ZotifyIn):
    """Скачать трек напрямую со Spotify (librespot, 320k с Premium)."""
    sp = body.spotify_url.strip()
    safe = body.safe.strip()
    if not _SPOTIFY_TRACK_RE.search(sp):
        raise HTTPException(status_code=400, detail="Нужна ссылка на трек Spotify (open.spotify.com/track/…)")
    if not config.zotify_configured():
        raise HTTPException(status_code=400, detail="Spotify не настроен (нет credentials.json)")
    job = jobs.enqueue_zotify(sp, safe, force_realtime=body.bulk)
    return {"job": job.to_dict()}


@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def api_upload(
    spotify_url: str = Form(...),
    safe: str = Form(...),
    file: UploadFile = File(...),
):
    """Заливка локального аудиофайла: получит метаданные/обложку со Spotify и
    ляжет в библиотеку по тому же пути, что и обычная загрузка."""
    spotify_url = spotify_url.strip()
    safe = safe.strip()
    if not _SPOTIFY_TRACK_RE.search(spotify_url):
        raise HTTPException(status_code=400, detail="Нужна ссылка на трек Spotify (open.spotify.com/track/…)")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не передан")

    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename)[1][:10] or ".audio"
    tmp_path = os.path.join(config.UPLOADS_DIR, f"{uuid.uuid4().hex}{ext}")
    try:
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
    finally:
        await file.close()

    # sync на обновление m3u ставит сама задача — только при успехе, без дублей
    job = jobs.enqueue_upload(tmp_path, spotify_url, safe, file.filename)
    return {"job": job.to_dict(), "filename": file.filename}


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def api_jobs(limit: int = 50):
    # limit: панель опрашивает с дефолтом (50, ей хватает — активные всегда сверху),
    # вкладка «Задачи» запрашивает больше (видеть всю очередь). active — реальное
    # число активных по ВСЕМУ реестру (а не только в окне выдачи), для счётчика в UI.
    lim = max(1, min(limit, 1000))
    # jobs — индивидуальные задачи (для синхронизации строк «Ненайденных» и вкладки);
    # groups — агрегаты пакетных загрузок (плейлист целиком) с прогрессом X/Y для панели.
    return {
        "jobs": jobs.list_jobs(lim),
        "active": jobs.active_count(),
        "groups": jobs.job_groups(),
    }


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
def api_cancel_job(job_id: str):
    if not jobs.cancel_job(job_id):
        raise HTTPException(status_code=409, detail="Задачу нельзя отменить (уже завершена/не найдена)")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def api_remove_job(job_id: str):
    if not jobs.remove_job(job_id):
        raise HTTPException(status_code=409, detail="Нельзя удалить активную задачу — сначала отмените")
    return {"ok": True}


class GroupRef(BaseModel):
    title: str


@app.post("/api/jobs-group/cancel", dependencies=[Depends(require_auth)])
def api_cancel_group(body: GroupRef):
    return {"ok": True, "cancelled": jobs.cancel_group(body.title)}


@app.post("/api/jobs-group/remove", dependencies=[Depends(require_auth)])
def api_remove_group(body: GroupRef):
    return {"ok": True, "removed": jobs.remove_group(body.title)}


@app.get("/api/status", dependencies=[Depends(require_auth)])
def api_status():
    return health.environment_status()


class SettingsIn(BaseModel):
    safe_mode: bool


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def api_get_settings():
    return settings.all_settings()


@app.post("/api/settings", dependencies=[Depends(require_auth)])
def api_set_settings(body: SettingsIn):
    return {"safe_mode": settings.set_safe_mode(body.safe_mode)}


# ---- массовый апгрейд качества (Step 2) ----
@app.get("/api/upgrade/status", dependencies=[Depends(require_auth)])
def api_upgrade_status():
    return upgrade.controller.status()


@app.post("/api/upgrade/start", dependencies=[Depends(require_auth)])
def api_upgrade_start():
    if not config.zotify_configured():
        raise HTTPException(status_code=400, detail="Сначала настройте Spotify (credentials.json)")
    started = upgrade.controller.start()
    return {"started": started, "status": upgrade.controller.status()}


@app.post("/api/upgrade/stop", dependencies=[Depends(require_auth)])
def api_upgrade_stop():
    upgrade.controller.stop()
    return {"status": upgrade.controller.status()}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def api_job(job_id: str, since: int = 0):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {"job": job.to_dict(include_log=True, since=since)}


# монтируем статику последней, чтобы не перехватывать /api
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
