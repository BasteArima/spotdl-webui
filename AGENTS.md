# AGENTS.md — handoff для ИИ-агента, продолжающего проект

> Прочитай целиком перед работой. Здесь то, что НЕ видно из кода: модель деплоя,
> выстраданные грабли, как тестировать, что секретно. UI и комментарии — на русском.

## 1. Что это

Самостоятельный веб-сервис в Docker: браузерный UI для управления локальной
музыкальной библиотекой, которую качает **spotdl** из Spotify (аудио берётся со
сторонних площадок — Spotify не отдаёт файлы напрямую). Музыку раздаёт уже
работающий **Navidrome** (его не трогаем). Главные функции:
- управление `playlists.txt` (формат `Имя|ссылка`), запуск `spotdl sync`;
- **ручное добивание** треков, которые автопоиск не нашёл на YouTube;
- несколько источников аудио (YouTube / Deezer / Spotify-напрямую / заливка файла);
- **массовый апгрейд** библиотеки до 320k (Step 2).

Стек: **FastAPI (Python) + ванильный JS SPA** (без фреймворков), один Docker-контейнер.
FastAPI дёргает spotdl/librespot/ffmpeg как **подпроцессы** (чтобы тяжёлые импорты
не висели в веб-процессе). Тёмная тема под Navidrome.

## 2. Модель деплоя (ВАЖНО — неочевидно)

- **Эта Windows-машина — только разработка/тесты.** Боевой сервер отдельный:
  **OpenMediaVault, Docker + Portainer.**
- **CI/CD:** push в `main` → **GitHub Actions** ([.github/workflows/build.yml](.github/workflows/build.yml))
  собирает образ и пушит в **GHCR** (`ghcr.io/bastearima/spotdl-webui:latest`, пакет
  публичный) → в Portainer пользователь жмёт **Recreate с Re-pull**. Workflow
  собирает обычным `docker build` + inline-кэш (НЕ `setup-buildx-action` — он падал
  по таймауту к Docker Hub при тяге `moby/buildkit`).
- Локально образ `FROM python:3.12-slim-bookworm` собрать можно, но боевой деплой —
  только на сервере. Тома `/srv/dev-disk-by-uuid-…` — серверные.
- В контейнере тома: `/conf` (данные spotdl) и `/music` (библиотека). Сервис бежит
  под **998:100** (тот же uid, что Navidrome), umask 002, дроп привилегий в
  [entrypoint.sh](entrypoint.sh) через gosu.
- Память агента (факты проекта) лежит в
  `~/.claude/projects/C--Users-user-Desktop-code-projects-spotdl-webui/memory/`
  (`MEMORY.md` + `deploy-environment.md`, `deezer-fallback.md`, `zotify-upgrade.md`).
  В Claude Code `MEMORY.md` подгружается автоматически.

## 3. Источники аудио (как добить трек)

1. **YouTube (spotdl)** — основной. Кнопка «Скачать» по ссылке YT/YT-Music из поля.
2. **Deezer** ([app/deezer.py](app/deezer.py), [deezer_dl.py](app/deezer_dl.py)) — по
   **ISRC** (точное совпадение), фолбэк — поиск по «артист+название» если нет ISRC.
   Нужен **ARL-токен** (секрет!) в `/conf/deezer_arl.txt` или env `DEEZER_ARL`.
   Расшифровка Blowfish, реализована сами (requests + pycryptodome). Free-аккаунт=128k.
3. **Spotify напрямую** ([app/librespot_dl.py](app/librespot_dl.py) — ядро,
   [spotify_dl.py](app/spotify_dl.py) — одноразовый CLI) через **librespot**: реальное
   аудио со Spotify, **320k с Premium**. Берёт аудио по track-id через content_feeder
   (НЕ через `api.spotify.com` — он даёт стойкий 429!). Метаданные/обложку даёт spotdl.
   Кнопка «Spotify 320k». Нужны креды (см. §5).
4. **Заливка файла** ([place_localfile.py](app/place_localfile.py)) — кнопка «📁 Файл»:
   локальный файл → мета/обложка со Spotify → в библиотеку.
5. **Liked Songs** — запись `Имя|saved` в `playlists.txt`. Данные треков сервис
   собирает САМ через токен пользователя (подпроцесс `app.spotify_login
   --saved-songs <файл>`: ТОЛЬКО страницы «Любимых» по 50 с паузой 1 с — ~17
   запросов на 800) в файл формата spotdl (список Song-словарей; genres=[], без
   лейбла/копирайта), дальше
   `spotdl download <файл> --save-file … --m3u … --save-errors …`
   (`jobs.liked_args`). Отвергнуто на практике: `sync saved --user-auth` (~3
   запроса/трек через dev-приложение → 429 со штрафом ~сутки) и `sync url1 url2…`
   (spotdl разбирает ссылки по одной, ~20 с на трек → часы подготовки), а также
   пакетные `/artists` (50) + `/albums` (20) ради жанров/лейбла — два свежих
   приложения подряд получили 429 с Retry-After ~21 ч. Штраф запоминается в
   `/conf/.spotify-app-ratelimit.json` (по client_id): пока он действует, запросы
   не шлются (иначе автосинк его продлевает); успешный вход файл снимает.
   spotipy создаётся с `retries=0` и непустым списком без 429 — иначе спит
   Retry-After внутри задачи.
   Токен создаёт вход из UI («Настройки» → «Войти в
   Spotify»): веб строит authorize-URL сам (`spotify_login.authorize_url`, с `state`
   в памяти, TTL 15 мин), пользователь вставляет адрес `127.0.0.1:9900/?code=…`,
   обмен code — в ПОДПРОЦЕССЕ `python -m app.spotify_login --exchange` (code через
   stdin, ответ — ASCII-JSON последней строкой), чтобы spotipy не жил в веб-процессе.
   CLI-вход `docker exec -it … python -m app.spotify_login` оставлен запасным.
   Redirect `127.0.0.1:9900` и scope — как у spotdl, иначе spotipy сочтёт кэш
   невалидным. Без токена sync этой записи
   пропускается ДО сброса errors-файла. Нужно СВОЁ приложение Spotify: общий
   client_id spotdl в dev-режиме не пускает чужих пользователей.
6. **Резолв имён** ([resolve_names.py](app/resolve_names.py), `/api/resolve-names`):
   когда spotdl записал в errors битое имя (`musicShelfRenderer`), фронт лениво
   дорезолвливает настоящее имя со Spotify (публичный клиент, логин не нужен).

Все «альтернативные» источники кладут файл через общий [app/library.py](app/library.py)
`place_file()` (ffmpeg-конвертация в формат библиотеки + теги/обложка + лёгкая
дозапись в m3u без полного sync). Формат — **mp3 320k** (`UPLOAD_BITRATE=320k`).

## 4. Анти-бан (критично для Spotify-источника!)

- **Real-time скачивание**: librespot-поток тормозится под длительность трека
  (`librespot_dl._pace_sleep`, идентично Zotify `--download-real-time`). Качается
  ~со скоростью прослушивания → «как живой слушатель».
- **Idle-пауза между треками** (`jobs.bulk_pause`, `ZOTIFY_BULK_WAIT_MIN/MAX`=5/15с)
  поверх real-time (аналог Zotify `bulk_wait_time`).
- Кто когда: **массовая закачка + апгрейд → всегда real-time + пауза**; одиночные
  «Spotify 320k» → real-time только при включённой галочке **«Безопасный режим»**
  (тумблер в шапке UI, `ZOTIFY_SAFE_MODE`, хранится в `/conf/.webui-settings.json`).
- Риск бана аккаунта Spotify остаётся ненулевым — это эвристики. Пользователь
  принял риск (свой Premium). Можно `UPGRADE_PER_DAY` (лимит в сутки).

## 5. Секреты (НИКОГДА не коммитить, не вшивать в образ)

- Вход в UI ([app/auth.py](app/auth.py)): `AUTH_ENABLED=false` — без пароля (только
  локалка); пароль из env `APP_PASSWORD` (старое имя `APP_AUTH_TOKEN`) важнее UI;
  иначе first-run setup в UI, хэш PBKDF2 в `/conf/.webui-auth.json` (там же секрет
  подписи). Браузер хранит НЕ пароль, а токен `v1.<exp>.<hmac>` (30 дней); в ключ
  подписи подмешан отпечаток пароля → смена пароля разлогинивает всех. Сырой
  env-пароль в `Authorization` тоже принимается (совместимость со старыми
  клиентами). Анти-перебор: 5 ошибок/IP → 10 мин. `/api/auth/status` — без авторизации.
- `/conf/cookies.txt`, `/conf/deezer_arl.txt` — пишутся из вкладки «Настройки» (mode 600).
- `/conf/cookies.txt` — YouTube cookies (Netscape).
- `/conf/deezer_arl.txt` или `DEEZER_ARL` — Deezer ARL.
- `/conf/spotify_app.json` (пишется из вкладки «Настройки», `settings.set_spotify_app`,
  mode 600; секрет в API наружу не отдаётся) / `SPOTIFY_CLIENT_ID`+`SPOTIFY_CLIENT_SECRET`
  (env важнее UI) — своё Spotify-приложение; смена client_id удаляет токен входа; `/conf/.spotify-user-token.json` — OAuth-токен пользователя
  (refresh). `--client-secret` маскируется в логах задач (`jobs._redact`).
- `/conf/zotify_credentials.json` — креды librespot. Пользователь входит в Spotify
  **через Facebook** → username/password НЕ работает. Только **OAuth** (PKCE),
  client_id keymaster `65b708073fc0480ea92a077233ca87bd`, redirect
  `http://127.0.0.1:5588/login`. Из UI: «Настройки» → «Spotify напрямую»
  ([zotify_login.py](app/zotify_login.py)): веб сам генерирует PKCE verifier/challenge
  и state (в памяти, TTL 15 мин), обмен — подпроцесс `python -m app.zotify_login
  --exchange` (POST за токеном → публичный `OAuth.ingest_token_response` →
  `Session.Builder` пишет креды во ВРЕМЕННЫЙ файл → `os.replace` только при успехе).
  Список scope скопирован из librespot (там приватный). Консольный запасной
  вариант: `docker exec -it spotdl-webui python -m app.gen_zotify_creds`. Локально
  librespot не импортируется из-за protobuf — для тестов
  `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`.

## 6. Настройки ([app/settings.py](app/settings.py))

- Реестр `FIELDS`: ключ, env-имя, тип, диапазон, подпись, группа. UI-карточки
  строятся из `/api/settings/schema` → новая настройка = одна запись в `FIELDS`.
- Приоритет обычных настроек: **UI → env → встроенный дефолт** (env = дефолт).
  Переопределения — `/conf/.webui-settings.json`; значение, равное дефолту, не
  хранится. Секреты (пароль, Spotify-приложение, Deezer ARL) — наоборот, **env
  важнее UI**.
- Код читает значения через `settings.get("ключ")` В МОМЕНТ ИСПОЛЬЗОВАНИЯ (не
  копирует в константы), поэтому изменения действуют без рестарта; планировщик
  автосинка сверяется с настройками раз в минуту. Подпроцессы (librespot_dl и
  т.п.) читают тот же json сами.
- Только-env (пути, шаблон, PUID/PGID) — в UI read-only (`settings.system_info`).
- Параметры spotdl для YouTube (`--bitrate`, `--audio`, `--lyrics`/`--generate-lrc`,
  `--overwrite`) собираются в `jobs._common_output_args`
  / `sync_args` из настроек. Раньше не передавались вовсе → spotdl кодировал в
  128k. `--lyrics` без значений = не искать тексты; `.lrc` требует провайдера
  `synced` (добавляется автоматически). Флаги со списком значений (nargs="*")
  идут ПОСЛЕ позиционного запроса — иначе съедят его.
- Лок плейлиста «живой»: владелец обновляет mtime раз в минуту, протухшим считается
  лок без обновления > 5 мин (раньше — 30 мин от создания, и часовой синк получал
  двойника). При старте сервиса `clear_stale_locks()` сносит все локи. Второй
  sync того же плейлиста в ЭТОМ процессе (`_active_syncs`) сразу пропускается
  (`AlreadySyncing`), а не ждёт — обычно это ручной Sync во время автосинка.
- `spotdl sync <URL> --save-file` (как и старый автосинк) НИЧЕГО не удаляет —
  удаление есть только в режиме `spotdl sync <file>.spotdl`. Поэтому настройки
  «удалять убранные треки» нет.

- **Navidrome** ([app/navidrome.py](app/navidrome.py)): любая успешная задача
  (воркер, `finally`) и каждый трек апгрейда зовут `navidrome.notify_changed()` —
  это только флаг. Скан (Subsonic `startScan`, токен md5(pass+salt)) запускает
  `maybe_scan` из тика планировщика (раз в минуту), когда нет активных задач,
  прошло `QUIET_SECONDS` затишья и минул `min_interval` с прошлого скана.
  Нужен админ Navidrome; `test()` проверяет `getUser.adminRole`. Подключение:
  env `NAVIDROME_*` важнее `/conf/navidrome.json`. Без зависимостей (urllib).

## 7. Очередь задач и апгрейд

- [app/jobs.py](app/jobs.py): две дорожки воркеров — **interactive** (ручные действия)
  и **background** (тяжёлый автосинк/«Синхронизировать всё»). Файловый лок на плейлист
  (`/conf/.webui-locks/`). Логи задач в памяти (cap 4000 строк) + дублируются в
  `docker logs`. UI статуса (плавающей панели задач больше нет):
  **строка статуса** вверху (`renderStatusStrip`: синки с прогрессом, загрузки,
  апгрейд, иначе «всё спокойно»), **карточки плейлистов** (живой статус из
  `/api/jobs` → `syncs`, сводка из `/api/playlists`: треков по save-файлу, не найдено,
  итог последнего синка из `/conf/.webui-playlist-stats.json`), вкладка
  **«Активность»** (понятные строки, лог по клику, инкрементальный рендер — чтобы не
  сбрасывать выделение). Прогресс синка — `jobs.SyncProgress` по выводу spotdl
  `--simple-tui` («K/N complete», «трек: Done/Skipped»; ошибки = обработано −
  скачано − пропущено; из них `LookupError:` — «не найдено», `AudioProviderError:` —
  «YouTube не отдал»: найден, но yt-dlp не скачал; 8 подряд = вероятный блок YouTube,
  пишется пояснение в лог и `yt_blocked` в итог; после синка — одна проба
  `python -m yt_dlp --simulate` по последней такой ссылке (`jobs._youtube_probe`):
  spotdl прячет настоящую ошибку yt-dlp в DEBUG, а проба даёт её в лог и
  `yt_reason` → подсказка на карточке). Синкам (не ручным загрузкам) идёт
  `--yt-dlp-args=--sleep-requests 1 --sleep-interval N --max-sleep-interval 2N` (`youtube_sleep`, 10 с):
  без пауз YouTube на ~400-м треке подряд перестал отдавать аудио. Подпроцессам ставится `COLUMNS=1000`: rich иначе рвёт
  длинные строки на 80 колонок.
- **Step 2 — апгрейд** ([app/upgrade.py](app/upgrade.py)): фоновый поток проходит по
  всем трекам библиотеки (union из `*.spotdl` в `/conf`), качает 320k, заменяет файл.
  Метка «уже 320k» персистентна (`/conf/.webui-upgraded.json`) → не качает повторно,
  переживает рестарт. Пропуск уже-хороших файлов по битрейту (`UPGRADE_MIN_BITRATE=300`).
  **Долгоживущий воркер** ([app/spotify_worker.py](app/spotify_worker.py)) — ОДНА
  авторизация librespot на весь сеанс (вместо новой на трек): меньше нагрузки и
  ban-риска. Контроллер общается с ним по stdin(URL)/stdout(JSON), result-queue +
  reader/stderr-потоки, таймаут `UPGRADE_TRACK_TIMEOUT`(1200с), авто-рестарт.
  UI: вкладка «Апгрейд 320k» (старт/стоп, прогресс, ETA).

## 8. Выстраданные грабли (не сломай!)

- **НЕ** использовать образ `spotdl/spotify-downloader:latest` (падает на `libresolv.so.2`).
  База — `python:3.12-slim-bookworm`.
- **deno** ставить РЕАЛЬНЫМ файлом в `/usr/local/bin` (не симлинком в `/root` —
  uid 998 туда не пускает). **git** нужен в apt (для `pip install` librespot/zotify из github).
- **cwd=/** для запуска spotdl: spotdl 4.5.0 sanitize вырезает `/` из пути `--m3u` →
  относительный путь от cwd. Из `/` резолвится в `/music/...`. Python-подпроцессы
  (spotify_dl и т.п.) запускаются с **cwd=/app** (для `python -m app.x`).
- spotdl пишет `--save-errors` в режиме ДОЗАПИСИ → дубли. errors_parser дедуплицирует
  + перед каждым sync errors-файл сбрасывается. Сброс = переименование в `.prev`: если spotdl
  оборвался (rc≠0, новый файл не записан), прежний возвращается (`_finish_errors_file`).
  Убийство сигналом (rc<0, не таймаут/отмена) пишется в лог с подсказкой про mem_limit:
  OOM в cgroup убивает spotdl, а не контейнер (`OOMKilled` = false).
- m3u пути на Linux **абсолютные** (`/music/...`); на Windows `Path` ведёт себя иначе —
  локальные тесты путей вводят в заблуждение.
- spotdl sync пишет в m3u ВЕСЬ список плейлиста (не только скачанное) → добитый трек
  попадает в m3u при следующем sync.

## 9. Как тестировать локально (Windows)

- Можно: парсинг (`python tests/test_naming.py`, 10 тестов), логику jobs/upgrade/parser
  со стабами, IPC воркера со стаб-скриптом, Deezer-поиск/скачивание вживую (ARL есть в
  истории чата, но лучше попросить заново), резолв имён вживую (публичный spotdl-клиент).
- Нельзя без кред/сервера: librespot-стрим (нужен credentials.json + риск аккаунта),
  ffmpeg может отсутствовать локально, `cwd="/app"` не существует на Windows (подпроцессы
  с этим cwd локально падают — это НЕ баг, на сервере ок).
- Запуск локально: `CONF_DIR=./_d/conf MUSIC_DIR=./_d/music APP_AUTH_TOKEN=dev
  AUTOSYNC_INTERVAL_HOURS=0 python -m uvicorn app.main:app --port 8000`.
- Проверки перед коммитом: `python -c "import ast,glob;[ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('app/*.py')]"`,
  `node --check app/static/app.js`, `python tests/test_naming.py`,
  `python -c "import yaml;yaml.safe_load(open('docker-compose.yml'))"`.

## 10. Конвенции

- UI-текст и комментарии — **на русском**.
- Коммит/пуш — **только по явной просьбе пользователя**. Он на `main` (это деплой-ветка,
  с неё CI). Коммитить **без** трейлера `Co-Authored-By` и любой атрибуции ИИ (явное требование пользователя).
- При фиксах больших фич — был прогон `/code-review` перед запуском апгрейда. Полезно
  повторять для рискованных изменений (тысячи треков).
- PowerShell vs Bash: для многострочного `git commit -m` в Bash используй `-F -` с
  here-doc (НЕ PowerShell `@'...'@` — он влезет `@` в сообщение).

## 11. Структура

```
app/main.py          FastAPI, маршруты, проверка доступа (токен сессии в заголовке)
app/auth.py          режимы входа, хэш пароля, токены сессий, анти-перебор
app/config.py        пути/шаблоны/флаги из env
app/jobs.py          очередь (2 дорожки), воркеры, лок, запуск spotdl, throttle, планировщик
app/naming.py        safe-имя/md5-id/тип URL (покрыто тестами!)
app/playlists.py     playlists.txt
app/errors_parser.py разбор errors/*.txt (+дедуп), удаление добитых
app/health.py        проверка окружения (cookies/права/deno) → баннер
app/library.py       place_file: путь+ffmpeg+теги+обложка+m3u; target_path/file_bitrate
app/librespot_dl.py  ядро скачивания через librespot (сессия снаружи, real-time)
app/spotify_dl.py    одноразовый CLI скачивания (ручные кнопки)
app/spotify_worker.py долгоживущий воркер (1 авторизация на апгрейд)
app/deezer.py        Deezer: ISRC/поиск + скачивание+расшифровка
app/deezer_dl.py     Deezer-фолбэк CLI
app/place_localfile.py заливка локального файла CLI
app/resolve_names.py резолв реальных имён со Spotify CLI
app/zotify_login.py  OAuth-вход librespot (PKCE): ссылка, обмен code (--exchange), статус
app/navidrome.py     пересканирование Navidrome после изменений (дебаунс + потолок частоты)
app/gen_zotify_creds.py то же из консоли (запасной вариант)
app/spotify_login.py OAuth-вход для Liked Songs: authorize-URL, обмен code (--exchange), CLI
app/settings.py      реестр настроек UI (UI→env→дефолт), секреты-файлы, cookies
app/upgrade.py       Step 2: массовый апгрейд (контроллер + воркер)
app/static/          index.html, app.js, style.css (тёмная SPA)
Dockerfile           самодостаточный образ (ffmpeg+spotdl+deno+git+app)
.github/workflows/build.yml  CI → GHCR
docker-compose.yml   стек для Portainer (image из GHCR), env с комментариями
README.md            короткий вход: возможности, быстрый старт, первая настройка
docs/                installation / configuration / sources / troubleshooting / development
                     (при новой настройке/фиче обновлять configuration.md и sources.md)
```

## 12. Состояние / возможные следующие задачи

- Сделано: все источники, анти-бан (real-time+пауза), Step 2 с долгоживущим воркером,
  резолв битых имён, фиксы из code-review.
- Опционально (из ревью, не критично): общий хелпер для одинаковых хвостов
  `_run_deezer/_run_zotify/_run_upload`; таблица источник→endpoint в JS вместо дублей.
- **FLAC-апгрейд** — обсуждался: возможен той же схемой, но FLAC даёт ТОЛЬКО платный
  lossless-сервис (Deezer HiFi / Qobuz / Tidal по ISRC, streamrip/deemix). Spotify FLAC
  не отдаёт. Нужен платный аккаунт — пока не делаем.
- **Запрошено: редизайн UI/UX под Apple Human Interface Guidelines** (см. ниже).

### Задача: редизайн под Apple HIG
Цель — переосмыслить [app/static/style.css](app/static/style.css) (и при нужде разметку
[index.html](app/static/index.html)) в стиле Apple: системный шрифт
`-apple-system, BlinkMacSystemFont, "SF Pro"`, больше воздуха, мягкие тени, скругления
~10–12px, спокойная палитра (light/dark по prefers-color-scheme), сегментированные
контролы вместо вкладок-кнопок, аккуратные «капсульные» кнопки с состояниями
hover/active, тонкие разделители, таблицы → карточки/списки в духе iOS Settings.
Логику/JS не ломать (id/классы, на которые завязан app.js, сохранять или править
синхронно). Делать инкрементально, проверять `node --check app/static/app.js`.
```
