"""FastAPI-приложение: API для управления плейлистами, просмотра ненайденных
треков и добивания. Аутентификация — токен в заголовке (Bearer / X-Auth-Token).
Токен НИКОГДА не передаётся в URL/query."""
import hmac
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, errors_parser, jobs, playlists

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


class RetryIn(BaseModel):
    safe: str


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


@app.post("/api/retry", dependencies=[Depends(require_auth)])
def api_retry(body: RetryIn):
    safe = body.safe.strip()
    if not safe:
        raise HTTPException(status_code=400, detail="Не указан плейлист")
    job = jobs.enqueue_retry(safe)
    return {"job": job.to_dict()}


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def api_jobs():
    return {"jobs": jobs.list_jobs()}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def api_job(job_id: str, since: int = 0):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {"job": job.to_dict(include_log=True, since=since)}


# монтируем статику последней, чтобы не перехватывать /api
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
