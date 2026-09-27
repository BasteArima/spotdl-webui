"""FastAPI-приложение: API для управления плейлистами, просмотра ненайденных
треков и добивания. Аутентификация — токен в заголовке (Bearer / X-Auth-Token).
Токен НИКОГДА не передаётся в URL/query."""
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (auth, config, errors_parser, health, jobs, navidrome, playlists, settings,
               spotify_login, upgrade, zotify_login)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# корень проекта для `python -m app.…` (в контейнере это /app)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="spotdl web-ui", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup() -> None:
    if not auth.enabled():
        print("[auth] ВНИМАНИЕ: авторизация отключена (AUTH_ENABLED=false) — UI открыт "
              "всем, кто достучится до порта. Только для закрытой локальной сети.", flush=True)
    elif auth.setup_required():
        print("[auth] пароль не задан — сервис попросит придумать его при первом входе. "
              "Если порт смотрит в интернет, задайте APP_PASSWORD в env.", flush=True)
    jobs.clear_stale_locks()   # остались от рестарта посреди синка — сейчас синков нет
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


def _request_token(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token"),
) -> str:
    return _extract_token(authorization, x_auth_token)


def require_auth(token: str = Depends(_request_token)) -> None:
    if not auth.is_authorized(token):
        raise HTTPException(status_code=401, detail="Требуется авторизация")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


class PasswordIn(BaseModel):
    password: str


class PasswordChangeIn(BaseModel):
    current: str
    new: str


@app.get("/api/auth/status")
def api_auth_status(token: str = Depends(_request_token)):
    """Без авторизации: фронту нужно понять, что показывать (вход / первичная
    установка пароля / сразу приложение)."""
    return {
        "enabled": auth.enabled(),
        "setup_required": auth.setup_required(),
        "source": auth.source(),
        "authenticated": auth.is_authorized(token),
        "min_length": auth.MIN_PASSWORD_LEN,
    }


@app.post("/api/auth/login")
def api_auth_login(body: PasswordIn, request: Request):
    if not auth.enabled():
        return {"token": ""}
    if auth.setup_required():
        raise HTTPException(status_code=409, detail="Пароль ещё не задан")
    ip = _client_ip(request)
    wait = auth.throttled(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Слишком много попыток — подождите {-(-wait // 60)} мин")
    if not auth.check_password(body.password):
        auth.register_failure(ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")
    auth.register_success(ip)
    return {"token": auth.issue_token()}


@app.post("/api/auth/setup")
def api_auth_setup(body: PasswordIn):
    try:
        return {"token": auth.setup(body.password)}
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/password", dependencies=[Depends(require_auth)])
def api_auth_change_password(body: PasswordChangeIn, request: Request):
    ip = _client_ip(request)
    if auth.throttled(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток — подождите")
    if not auth.check_password(body.current):
        auth.register_failure(ip)
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")
    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # старые сессии (и эта) после смены пароля недействительны — выдаём новую
    return {"token": auth.issue_token()}


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
    if len(_NAME_CACHE) > 5000:      # кэш имён не должен расти бесконечно
        _NAME_CACHE.clear()
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
    # Снимок кэшируется по ревизии реестра: пока ничего не менялось, опрос панели
    # не гоняет три прохода по всему реестру заново.
    return jobs.snapshot(lim)


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


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def api_get_settings():
    return settings.all_settings()


@app.post("/api/settings", dependencies=[Depends(require_auth)])
def api_set_settings(body: Dict[str, Any]):
    """Частичное обновление: {"ключ": значение}; null — сбросить к env/дефолту."""
    try:
        return settings.update(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/settings/schema", dependencies=[Depends(require_auth)])
def api_settings_schema():
    return {
        "fields": settings.schema(),
        "system": settings.system_info(),
        "next_autosync": jobs.next_autosync(),
    }


class ArlIn(BaseModel):
    arl: str


@app.get("/api/deezer-arl", dependencies=[Depends(require_auth)])
def api_get_deezer_arl():
    return settings.deezer_status()      # сам ARL наружу не отдаём


@app.post("/api/deezer-arl", dependencies=[Depends(require_auth)])
def api_set_deezer_arl(body: ArlIn):
    try:
        return settings.set_deezer_arl(body.arl)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/deezer-arl", dependencies=[Depends(require_auth)])
def api_clear_deezer_arl():
    try:
        return settings.clear_deezer_arl()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class NavidromeIn(BaseModel):
    url: str = ""
    user: str = ""
    password: str = ""      # пусто = оставить сохранённый
    enabled: bool = True
    min_interval: int = 15


@app.get("/api/navidrome", dependencies=[Depends(require_auth)])
def api_navidrome_status():
    return navidrome.status()


@app.post("/api/navidrome", dependencies=[Depends(require_auth)])
def api_navidrome_save(body: NavidromeIn):
    try:
        return navidrome.save(body.url, body.user, body.password, body.enabled, body.min_interval)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/navidrome", dependencies=[Depends(require_auth)])
def api_navidrome_clear():
    try:
        return navidrome.clear()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/navidrome/test", dependencies=[Depends(require_auth)])
def api_navidrome_test():
    try:
        return navidrome.test()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/navidrome/scan", dependencies=[Depends(require_auth)])
def api_navidrome_scan():
    res = navidrome.scan(auto=False)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["message"])
    return navidrome.status()


@app.get("/api/cookies", dependencies=[Depends(require_auth)])
def api_get_cookies():
    return settings.cookies_status()


@app.post("/api/cookies", dependencies=[Depends(require_auth)])
async def api_set_cookies(file: UploadFile = File(...)):
    try:
        raw = await file.read(settings._COOKIES_MAX + 1)
    finally:
        await file.close()
    try:
        return settings.set_cookies(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/cookies", dependencies=[Depends(require_auth)])
def api_clear_cookies():
    return settings.clear_cookies()


class SpotifyAppIn(BaseModel):
    client_id: str
    client_secret: str = ""   # пусто = оставить сохранённый


@app.get("/api/spotify-app", dependencies=[Depends(require_auth)])
def api_get_spotify_app():
    return settings.spotify_app_status()


@app.post("/api/spotify-app", dependencies=[Depends(require_auth)])
def api_set_spotify_app(body: SpotifyAppIn):
    try:
        return settings.set_spotify_app(body.client_id, body.client_secret)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/spotify-app", dependencies=[Depends(require_auth)])
def api_clear_spotify_app():
    try:
        return settings.clear_spotify_app()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- вход в Spotify (Liked Songs) ----
# state OAuth-входа: защищает от вставки чужого/старого redirect-адреса.
# Один на процесс — вход делает один человек, новая попытка вытесняет старую.
_LOGIN = {"state": "", "ts": 0.0}
_LOGIN_TTL = 15 * 60


class SpotifyLoginIn(BaseModel):
    redirect_url: str


@app.post("/api/spotify-login/start", dependencies=[Depends(require_auth)])
def api_spotify_login_start():
    cid = config.spotify_app()[0]
    if not cid:
        raise HTTPException(status_code=400, detail="Сначала сохраните Client ID и secret своего приложения")
    _LOGIN.update(state=secrets.token_urlsafe(16), ts=time.time())
    return {"url": spotify_login.authorize_url(cid, _LOGIN["state"]),
            "redirect_uri": spotify_login.REDIRECT_URI}


@app.post("/api/spotify-login/finish", dependencies=[Depends(require_auth)])
def api_spotify_login_finish(body: SpotifyLoginIn):
    code, state, error = spotify_login.parse_redirect(body.redirect_url)
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify отклонил вход: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="В адресе нет code= — скопируйте адрес целиком")
    if not _LOGIN["state"] or time.time() - _LOGIN["ts"] > _LOGIN_TTL:
        raise HTTPException(status_code=400, detail="Попытка входа устарела — нажмите «Войти в Spotify» ещё раз")
    # голый code (без state) принимаем: его вставляет сам пользователь
    if state and not hmac.compare_digest(state, _LOGIN["state"]):
        raise HTTPException(status_code=400, detail="Адрес от другой попытки входа — нажмите «Войти в Spotify» ещё раз")
    # обмен — в подпроцессе (spotipy не держим в веб-процессе); code через stdin
    try:
        r = subprocess.run([sys.executable, "-m", "app.spotify_login", "--exchange"],
                           cwd=PROJECT_DIR, input=code + "\n", capture_output=True,
                           text=True, timeout=60)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        res = json.loads(lines[-1]) if lines else {}
    except subprocess.TimeoutExpired:
        res = {"ok": False, "error": "Spotify не ответил за 60 с"}
    except ValueError:
        res = {"ok": False, "error": "неожиданный ответ: " + ((r.stderr or "").strip()[-300:] or "пусто")}
    _LOGIN.update(state="", ts=0.0)   # code одноразовый — попытка израсходована
    if not res.get("ok"):
        detail = res.get("error") or "вход не удался"
        if res.get("hint"):
            detail += ". " + res["hint"]
        raise HTTPException(status_code=400, detail=detail)
    settings.set_spotify_user(res.get("user", ""))
    return dict(settings.spotify_app_status(), total=res.get("total"))


@app.delete("/api/spotify-login", dependencies=[Depends(require_auth)])
def api_spotify_logout():
    return settings.logout_spotify()


# ---- вход librespot / Zotify («Spotify 320k» и апгрейд) ----
# PKCE-секрет и state попытки — в памяти веба; обмен code — в подпроцессе
# (librespot не грузим в веб-процесс).
_ZLOGIN = {"state": "", "verifier": "", "ts": 0.0}


@app.get("/api/zotify-creds", dependencies=[Depends(require_auth)])
def api_zotify_status():
    return zotify_login.status()


@app.post("/api/zotify-login/start", dependencies=[Depends(require_auth)])
def api_zotify_login_start():
    _ZLOGIN.update(state=secrets.token_urlsafe(16), verifier=zotify_login.new_verifier(), ts=time.time())
    return {"url": zotify_login.authorize_url(_ZLOGIN["verifier"], _ZLOGIN["state"]),
            "redirect_uri": zotify_login.REDIRECT_URI}


@app.post("/api/zotify-login/finish", dependencies=[Depends(require_auth)])
def api_zotify_login_finish(body: SpotifyLoginIn):
    code, state, error = spotify_login.parse_redirect(body.redirect_url)
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify отклонил вход: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="В адресе нет code= — скопируйте адрес целиком")
    if not _ZLOGIN["verifier"] or time.time() - _ZLOGIN["ts"] > _LOGIN_TTL:
        raise HTTPException(status_code=400, detail="Попытка входа устарела — нажмите «Войти» ещё раз")
    if state and not hmac.compare_digest(state, _ZLOGIN["state"]):
        raise HTTPException(status_code=400, detail="Адрес от другой попытки входа — нажмите «Войти» ещё раз")
    payload = json.dumps({"code": code, "verifier": _ZLOGIN["verifier"]})
    _ZLOGIN.update(state="", verifier="", ts=0.0)   # code одноразовый — попытка израсходована
    try:
        r = subprocess.run([sys.executable, "-m", "app.zotify_login", "--exchange"],
                           cwd=PROJECT_DIR, input=payload, capture_output=True,
                           text=True, timeout=90)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        res = json.loads(lines[-1]) if lines else {}
        if not res:
            res = {"ok": False, "error": "неожиданный ответ: " + ((r.stderr or "").strip()[-300:] or "пусто")}
    except subprocess.TimeoutExpired:
        res = {"ok": False, "error": "Spotify не ответил за 90 с"}
    except ValueError:
        res = {"ok": False, "error": "неожиданный ответ: " + ((r.stderr or "").strip()[-300:] or "пусто")}
    if not res.get("ok"):
        detail = res.get("error") or "вход не удался"
        if res.get("hint"):
            detail += ". " + res["hint"]
        raise HTTPException(status_code=400, detail=detail)
    return dict(zotify_login.status(), user=res.get("user") or zotify_login.status()["user"])


@app.delete("/api/zotify-creds", dependencies=[Depends(require_auth)])
def api_zotify_logout():
    if zotify_login.status()["source"] == "env":
        raise HTTPException(status_code=400, detail="Задано через env ZOTIFY_USERNAME/PASSWORD — удалить из UI нельзя.")
    return zotify_login.logout()


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
