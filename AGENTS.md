# AGENTS.md — handoff для ИИ-агента, продолжающего проект

> Прочитай целиком перед работой. Здесь то, что НЕ видно из кода: модель деплоя,
> выстраданные грабли, как тестировать, что секретно. UI и комментарии — на русском.
>
> **Новый чат = новая фича.** Пользователь начинает отдельный чат на каждую задачу.
> Порядок: этот файл → [design/plan.md](design/plan.md) (этапы и задачи, чекбоксы — что
> сделано и что дальше) → нужные модули из §11. Состояние и следующие шаги — §12.

## 1. Что это

Самостоятельный веб-сервис в Docker: личный бэкап и миграция музыки из Spotify (позже —
YouTube Music) плюс свои файлы. Сервис ведёт библиотеку сам — индекс SQLite
(`/conf/library.db`): какие файлы есть, откуда, в каких плейлистах, что не скачано, что
дубль. Площадки — источники списков треков; **spotdl** — один из загрузчиков (звук с
YouTube). Музыку раздаёт уже работающий **Navidrome** (его не трогаем), слушают в
**Arpeggi** (iPhone/iPad). Видение и решения пользователя — в памяти агента
(`product-vision.md`, `playlists-over-albums.md`: плейлисты важнее альбомов Navidrome).
Главные функции:
- плейлисты (в индексе; `playlists.txt` — только выгрузка) и синк через индекс: качается
  только недостающее, m3u собирает сервис;
- **добивание** того, чего нет на YouTube (Deezer / Spotify напрямую / своя ссылка / файл);
- **склейка дублей** с корзиной `/music/.trash`;
- **массовый апгрейд** библиотеки до 320k (Step 2).

Стек: **FastAPI (Python) + ванильный JS SPA** (без фреймворков), один Docker-контейнер.
FastAPI дёргает spotdl/librespot/ffmpeg как **подпроцессы** (чтобы тяжёлые импорты
не висели в веб-процессе). UI — светлая/тёмная тема, на телефоне вкладки снизу.

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

1. **YouTube (spotdl)** — основной: синк качает недостающее сам; вручную — «Своя ссылка YouTube» у трека.
2. **Deezer** ([app/deezer.py](app/deezer.py), [deezer_dl.py](app/deezer_dl.py)) — по
   **ISRC** (точное совпадение), фолбэк — поиск по «артист+название» если нет ISRC.
   Нужен **ARL-токен** (секрет!) в `/conf/deezer_arl.txt` или env `DEEZER_ARL`.
   Расшифровка Blowfish, реализована сами (requests + pycryptodome). Free-аккаунт=128k.
3. **Spotify напрямую** ([app/librespot_dl.py](app/librespot_dl.py) — ядро,
   [spotify_dl.py](app/spotify_dl.py) — одноразовый CLI) через **librespot**: реальное
   аудио со Spotify, **320k с Premium**. Берёт аудио по track-id через content_feeder
   (НЕ через `api.spotify.com` — он даёт стойкий 429!). Метаданные/обложку даёт spotdl.
   Источник «Spotify 320k» у трека. Нужны креды (см. §5).
4. **Заливка файла** ([place_localfile.py](app/place_localfile.py)) — источник «Свой файл»:
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
  («Настройки» → «Spotify напрямую», `ZOTIFY_SAFE_MODE`, хранится в `/conf/.webui-settings.json`).
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
- `/conf/cookies.txt`, `/conf/deezer_arl.txt` — пишутся из раздела «Настройки» (mode 600).
- `/conf/cookies.txt` — YouTube cookies (Netscape).
- `/conf/deezer_arl.txt` или `DEEZER_ARL` — Deezer ARL.
- `/conf/spotify_app.json` (пишется из раздела «Настройки», `settings.set_spotify_app`,
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
  (`/conf/.webui-locks/`). spotdl запускается через [app/spotdl_run.py](app/spotdl_run.py):
  в `sync <URL>` у треков плейлиста нет genres/disc_count, и spotdl ДО проверки «файл уже
  есть» делал reinit (Song.from_url, ~3 запроса к Spotify) — для КАЖДОГО скачанного трека;
  автосинк 52 плейлистов шёл ~сутки. Обёртка патчит `Downloader.async_search_and_download`
  (в spotdl 4.5+ загрузки идут через него, pool_download; `search_and_download` — только
  у старых версий, патч на нём молча не срабатывал): если файл есть (та же проверка, что
  у spotdl) — genres=[], disc_count заполняются, reinit нет. Только для `--overwrite skip`.
  С env `SPOTDL_WEBUI_RESULTS=1` (run_spotdl(results=True)) обёртка печатает итог каждого
  трека строкой `@@webui-result {json}` (url, path, existed, download_url, error) — её
  читает синк через индекс; в лог задачи строки не идут. Вывод spotdl в лог задачи/docker logs фильтрует
  `jobs.SpotdlLogFilter` (без строк на каждый пропущенный трек, рамок трейсбеков rich и
  сбоев поиска текстов; в конце плейлиста — строка `[итог]`), прогресс видит всё;
  настройка `verbose_spotdl_log` выключает фильтр. Группы в `job_groups` — только пакетные
  загрузки: повторные автосинки с одним заголовком раньше рисовались «пакетом 4/5».
  Логи задач в памяти (cap 4000 строк) + дублируются в
  `docker logs`. В новом UI статус — строка «что сейчас происходит» (`store.describeNow`),
  «Главная» и «Активность». В прежнем (/legacy):
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
  файлам библиотеки с треком Spotify (из индекса), качает 320k, заменяет именно этот файл.
  Метка «уже 320k» персистентна (`/conf/.webui-upgraded.json`) → не качает повторно,
  переживает рестарт. Пропуск уже-хороших файлов по битрейту (`UPGRADE_MIN_BITRATE=300`).
  **Долгоживущий воркер** ([app/spotify_worker.py](app/spotify_worker.py)) — ОДНА
  авторизация librespot на весь сеанс (вместо новой на трек): меньше нагрузки и
  ban-риска. Контроллер общается с ним по stdin(URL)/stdout(JSON), result-queue +
  reader/stderr-потоки, таймаут `UPGRADE_TRACK_TIMEOUT`(1200с), авто-рестарт.
  UI: раздел «Качество 320k» (старт/стоп, прогресс, ETA, последние треки).

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
- spotdl `download` возвращает 0 и при молчаливом провале («Error occurred while reinitializing
  song») — успех загрузки только по явному отчёту (jobs.looks_downloaded: «Downloaded "…"»,
  «: Done», «file already exists»), иначе трек выпадал из ненайденных без файла.
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
- На этом ПК тесты — через `C:\Users\user\miniconda3\python.exe` (в PATH первым стоит Python
  3.14 без spotdl). Все тесты: `for t in tests/test_*.py; do python "$t"; done`.
- Быстрые проверки UI/логики — на стенде Docker Desktop (`docker-compose.dev.yml`, :4536,
  данные в `_dev/`, вход выключен); Python-правки — `docker restart spotdl-webui-dev`.
- Проверка на боевом сервере (`192.168.0.105`: сервис :4535, Navidrome :4533, Portainer :9000) —
  через встроенный браузер; вход делает сам пользователь (пароли не вводить). Команды внутри
  боевого контейнера (Portainer exec) запрещены — только API сервиса и Navidrome.
- Версия на сервере — «Настройки» → «О системе» (коммит вшивает CI).
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
app/spotdl_run.py    обёртка запуска spotdl (через неё run_spotdl): скачанное — без дозапроса метаданных
app/settings.py      реестр настроек UI (UI→env→дефолт), секреты-файлы, cookies
app/db.py            индекс библиотеки (SQLite /conf/library.db): схема, миграции по PRAGMA user_version
app/scanner.py       скан /music → индекс: теги (WOAS=Spotify, COMM=ролик YouTube, TSRC=ISRC) → track_ids;
                     инкрементально по mtime+размеру; два файла с одним внешним ID → match_candidates
                     (уровень 1). Задача очереди «index» (фоновая дорожка): при старте, после синков,
                     POST /api/library/rescan. Исполнителей spotdl склеивает «/» — split_artists различает
                     по исполнителю альбома (HUNTR/X). spotdl не пишет ISRC в m4a. Файл с ID трека,
                     который ждал как «не скачан», сливается с ним (db.merge_tracks), а не в дубли.
app/legacy_import.py *.spotdl + errors/*.txt → playlist_items/missing — только для плейлистов, которые ещё
                     синкаются по-старому (managed=0; запасной способ spotdl sync); после каждого скана;
                     пропускает неизменённые; повторяемый по legacy_id.
                     Трек ищется по ID Spotify, потом по ISRC; иначе ряд «не скачан» (без пути).
app/matching.py      та же ли запись: parse_title → (основа, маркеры версии); feat/Official Video/
                     «from the series» отбрасываются, live/remaster/cover/ver./slowed — сравниваются
                     (разные маркеры = разные треки, решение пользователя). compare → уровень 1–4.
                     Поменял правила — увеличь NORM_VERSION (norm_key пересчитаются).
app/dedupe.py        проход по библиотеке после импорта: пары файлов → match_candidates (группы по
                     ISRC / norm_key / исполнителю, не перебор всех пар); «не скачан» + файл той же
                     записи (ур. 1–3) → db.merge_tracks. Файлы не трогает. Уровень 1 ставит сканер —
                     dedupe его не понижает.
tests/make_dev_library.py  тестовая библиотека для стенда docker-compose.dev.yml (:4536, данные в _dev/).
app/playlists.py     плейлисты: с этапа 3 главный список — в индексе (playlists + playlist_sources,
                     источник Spotify = ref ссылка/'saved'); один раз переносится из playlists.txt
                     (meta playlists_master=db), дальше playlists.txt — ВЫГРУЗКА после каждой
                     правки (для синка по-старому, /legacy и отката образа), ручные правки файла
                     не читаются. read_playlists()/find_by_url — из индекса; legacy_id = md5(ссылки).
app/index_sync.py    синк через индекс (sync_engine=index — по умолчанию с 3.5; spotdl — запасной): spotify_source → reconcile
                     (ID Spotify → ISRC → dedupe.link_missing ур. 1–3: файл другого релиза не качаем)
                     → `spotdl download <только недостающие>` с results=True → apply_results (путь /
                     слияние с уже проиндексированным файлом / причина в missing) → m3u_gen. Плейлист
                     получает playlists.managed=1: legacy_import его не трогает, m3u пишет сервис;
                     синк по-старому ставит managed=0. Пустой список не применяется (сбой площадки).
                     auto_fetch_candidates — «нет на YouTube» → Deezer/Авто (настройка auto_fetch),
                     попытка помечается в missing.tried, тот же источник — раз в неделю.
app/spotify_source.py список треков источника без загрузки (CLI-подпроцесс). spotdl 4.5 по умолчанию
                     ходит через SpotipyFree (веб-плеер, без dev-приложения): у треков плейлиста НЕТ
                     альбома, ISRC и tracks_count — сопоставление держится на ID Spotify, а для
                     скачивания spotdl всё равно делает reinit (3 запроса) — но только недостающих.
                     «Playlist not available or not accessible» — ответ SpotipyFree → unavailable.
app/m3u_gen.py       m3u для managed-плейлистов из индекса: только скачанное, по порядку источника,
                     формат и путь как у spotdl; переписывается только при изменении. Пишется в конце
                     синка и в конце задачи index (scanner CLI → m3u_written → Navidrome-скан).
app/merge.py         склейка дублей (4.4): решение «same» → merge_pair: лучший файл (lossless → битрейт →
                     больше плейлистов) остаётся, худший — в /music/.trash/<тот же путь> (+.lrc, в корзине
                     .ndignore для Navidrome); ряд худшего — status 'trashed' (trashed_from, merged_into),
                     плейлисты и track_ids — на оставшийся; m3u: managed — заново, прежние spotdl — заменой
                     строки (fix_m3u). auto_eligible: ур. 1–3 И (общий трек Spotify/Deezer — CATALOG_ID_REASONS,
                     это переименованный Spotify трек, скачанный дважды; ИЛИ сравнение БЕЗ общих ID даёт ур. ≤3) —
                     пары «общий ролик YouTube, разные названия» (spotdl взял один звук для двух песен) только
                     вручную. Разные альбомы склеиваются (решение пользователя: приоритет — плейлисты;
                     раздел «Альбомы» Navidrome берёт альбом из тега, запись будет у одного издания); это же правило у «Склеить все уверенные» (safe_only) и AUTO_MERGE=sure.
                     Задача очереди «merge» (python -m app.merge) и скан индекса — под db.library_lock
                     (fcntl, межпроцессный): скан иначе счёл бы перенесённый файл пропавшим. Сканер
                     игнорирует trashed-ряды и папку .trash. restore → пара decision='different'.
app/upgrade.py       Step 2: массовый апгрейд (контроллер + воркер). Список — из индекса (файлы с ID Spotify),
                     воркеру уходит «url\tпуть», librespot_dl заменяет ИМЕННО этот файл (target), а не путь по
                     шаблону — иначе после склейки/другого релиза рядом появлялся бы второй файл.
app/static/          index.html, app.js, style.css — прежний UI (на «/legacy», ссылка — в «Настройки» → «О системе»)
app/static/v2/       основной UI по design/prototype.html (на «/»; «/new» → редирект на «/»): index.html, app.css,
                     icons.svg (спрайт Tabler — только используемые иконки: новая = скачать
                     @tabler/icons из npm и дописать <symbol>), js/main.js (каркас, роутинг #/раздел,
                     вход), js/api.js, js/store.js (опрос /api/jobs + /api/library), js/ui.js,
                     js/views/*.js (render/mount/onState). Без сборщика — ES-модули. Ключи
                     localStorage (токен, тема) общие с прежним UI. /static и страницы — Cache-Control:
                     no-cache (ETag), чтобы после обновления не жил старый модуль.
                     js/missing.js — недокачанные: источник на трек (Авто/Deezer/Spotify/ссылка YT/файл)
                     и на все; API /api/v2/tracks/{id}/fetch|upload, /api/v2/playlists/{id}/fetch-missing.
                     «Авто» = jobs.enqueue_auto: Deezer по ISRC → Spotify напрямую. Экраны перечитывают
                     данные, когда store видит новое завершённое обновление индекса (а не «очередь
                     затихла» — скан короче интервала опроса).
                     views/attention.js — не скачано / дубли / источники / корзина. Решение «same» по
                     дублю ставит задачу склейки (app/merge.py); «уверенные» = isSure (level ≤ 3 и
                     safe с сервера). Источник «unavailable» ставит синк по ответу
                     Spotify «Playlist not available» (jobs._mark_source_availability); импорт
                     статус источника не трогает.
                     views/library.js (GET /api/v2/library: поиск — своя SQL-функция ulower,
                     т.к. LIKE в SQLite не знает регистра кириллицы; переход в альбом/исполнителя —
                     точный фильтр album/album_artist/artist), views/quality.js (GET /api/v2/quality;
                     апгрейд хранит последние 20 результатов в памяти — controller.recent).
                     Иконки: спрайт встраивается в страницу при старте (ui.loadIcons), <use href="#имя">.
                     views/activity.js — задачи, лог по клику (дописывается на лету), отмена/уборка;
                     пакеты (job_groups) — только загрузки треков (_BATCH_KINDS), не синки/индекс.
                     views/settings.js — разделы с полями реестра по ключам (PAGES); поле, не
                     попавшее ни в один раздел, само уходит в «Прочее» — новая настройка в FIELDS
                     видна сразу. Особые разделы: Spotify (приложение + вход), YouTube Music (cookies),
                     Deezer, librespot (вход), Navidrome, доступ (смена пароля → новый токен), система.
                     Прослушивание файла (дубли): <audio> не шлёт токен в заголовке, токен в URL не
                     кладём → POST /api/v2/tracks/{id}/listen выдаёт подписанную ссылку на ОДИН файл
                     на 15 мин (ключ — в памяти процесса), GET /api/v2/audio/{id}?exp&sig без входа,
                     Range работает (перемотка), путь проверяется на нахождение внутри MUSIC_DIR.
Dockerfile           самодостаточный образ (ffmpeg+spotdl+deno+git+app)
.github/workflows/build.yml  CI → GHCR
docker-compose.yml   стек для Portainer (image из GHCR), env с комментариями
README.md            короткий вход: возможности, быстрый старт, первая настройка
docs/                installation / configuration / sources / troubleshooting / development
                     (при новой настройке/фиче обновлять configuration.md и sources.md)
```

## 12. Состояние / возможные следующие задачи

- **Идёт переделка по [design/plan.md](design/plan.md)** (согласована с пользователем):
  сервис ведёт библиотеку сам (индекс SQLite), площадки — источники списков, spotdl —
  один из загрузчиков. Задачи — чекбоксы в плане; отмечать выполненные там же.
  Миграции `app/db.py`: только дописывать новые в конец.
- **Сделано:** этап 1 (индекс), этап 2 (новый UI на «/», прежний на «/legacy»), этап 3
  (синк через индекс — по умолчанию; проверен на боевой библиотеке: 52 плейлиста ~16 мин),
  4.4 (склейка дублей + корзина; уверенные пары склеивает пользователь кнопкой). Плюс: все
  источники, анти-бан, Step 2 с долгоживущим воркером (берёт файлы из индекса), версия
  образа в настройках.
- **Дальше по плану:** 4.1–4.3 (YouTube Music: «Понравившиеся» и плейлисты через ytmusicapi,
  загрузчик yt-dlp по videoId, плейлист из нескольких источников), этап 5 (поиск), этап 6
  (поиск в Arpeggi через прокси Subsonic).
- **Идеи из разбора дублей:** 22 пары «общий ролик YouTube, разные названия» — у одного файла
  не тот звук (spotdl взял один ролик для двух песен) → действие «перекачать из другого
  источника». Пары «тот же трек Spotify, разная длина» — подсказать, какая длина у Spotify.
- **FLAC-апгрейд** — возможен той же схемой, но нужен платный lossless-сервис (Deezer HiFi /
  Qobuz / Tidal по ISRC). Spotify FLAC не отдаёт. Пока не делаем.
- Прежний UI (`app/static/`) не развивать; новые функции — только в v2.
