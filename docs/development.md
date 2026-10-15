# Разработка

[← README](../README.md)

Подробная передача контекста для ИИ-агентов и новых разработчиков — [AGENTS.md](../AGENTS.md):
модель деплоя, секреты, грабли. Здесь — короткий обзор.

- [Архитектура](#архитектура)
- [Структура](#структура)
- [Как sync совпадает с автосинком](#как-sync-совпадает-с-автосинком)
- [Локальный запуск и тесты](#локальный-запуск-и-тесты)
- [CI](#ci)
- [Грабли](#грабли)

## Архитектура

- **FastAPI + ванильный JS** (без фреймворков и сборщика), один контейнер. UI — SPA на
  ES-модулях в `app/static/v2/` на `/`; прежний интерфейс (`app/static/index.html`, `app.js`)
  оставлен на `/legacy`.
- **Индекс библиотеки** — SQLite `/conf/library.db`: файлы, внешние ID, плейлисты и их
  источники, недокачанное, кандидаты в дубли. Задача «index» (сканер → импорт прежних файлов
  `playlists.txt`/save/errors → поиск дублей) идёт после синков и загрузок.
- **Тяжёлое — в подпроцессах:** spotdl, librespot, ffmpeg, обмен OAuth-кодов. Веб-процесс
  не держит их импорты в памяти простоя.
- **Очередь задач — две дорожки:** интерактивная (ручные действия) и фоновая (синк всех,
  автосинк), каждая со своим воркер-потоком. Ручное добивание не ждёт многочасовой синк.
  Один плейлист защищён файловым локом (`/conf/.webui-locks/`).
- **Планировщик** — поток, раз в минуту сверяется с настройками: автосинк и отложенный
  скан Navidrome.
- **Настройки** — реестр `FIELDS` в `settings.py`; карточки UI строятся из
  `/api/settings/schema`. Значения читаются в момент использования, поэтому меняются без
  рестарта. Приоритет: UI → env → встроенный дефолт; для секретов — env → UI.
- **Авторизация** — `auth.py`: хэш пароля PBKDF2, браузер хранит подписанный HMAC-токен
  сессии, режим без пароля — `AUTH_ENABLED=false`.

## Структура

```
app/
  main.py            FastAPI: маршруты, проверка доступа
  db.py              индекс библиотеки (SQLite): схема, миграции, слияние треков
  scanner.py         сканер /music → индекс (теги, ID, инкрементально)
  legacy_import.py   playlists.txt, save- и errors-файлы → индекс
  matching.py        нормализация названий, уровни совпадения треков
  dedupe.py          поиск кандидатов в дубли
  library_queries.py выборки для нового интерфейса
  spotdl_run.py      обёртка запуска spotdl (уже скачанное — без дозапроса метаданных)
  auth.py            режимы входа, хэш пароля, токены сессий, анти-перебор
  config.py          пути и системные параметры из env
  settings.py        реестр настроек UI (UI → env → дефолт), секреты-файлы, cookies
  jobs.py            очередь (2 дорожки), воркеры, локи, запуск spotdl, планировщик
  naming.py          safe-имя, id плейлиста, тип ссылки — покрыто тестами
  playlists.py       плейлисты: главный список в индексе, playlists.txt — выгрузка
  index_sync.py      синк через индекс: сверка списка с библиотекой, итоги загрузки, автодобивание
  spotify_source.py  список треков источника Spotify без загрузки (CLI для подпроцесса)
  m3u_gen.py         m3u плейлистов, которые синкает сервис, — из индекса
  errors_parser.py   разбор errors/*.txt (+ дедуп), удаление добитых
  health.py          проверка окружения для баннера
  library.py         место трека в библиотеке: путь по шаблону, ffmpeg, теги, обложка, m3u
  append_m3u.py      дозапись одного трека в m3u без полного sync
  place_localfile.py заливка своего файла
  deezer.py          Deezer: поиск по ISRC, скачивание, расшифровка
  deezer_dl.py       Deezer-фолбэк (CLI для подпроцесса)
  librespot_dl.py    ядро скачивания через librespot (real-time)
  spotify_dl.py      одиночная загрузка «Spotify 320k» (CLI)
  spotify_worker.py  долгоживущий воркер апгрейда: одна авторизация на сеанс
  upgrade.py         апгрейд библиотеки до 320k
  zotify_login.py    вход librespot (OAuth PKCE): ссылка, обмен кода
  gen_zotify_creds.py  то же из консоли
  spotify_login.py   вход для Liked Songs (spotdl `saved`)
  navidrome.py       пересканирование Navidrome после изменений
  resolve_names.py   настоящие имена треков вместо битых в errors
  static/v2/         основной интерфейс: index.html, app.css, icons.svg, js/ (main, api, store, ui, views/)
  static/            прежний интерфейс на /legacy: index.html, app.js, style.css
tests/               test_naming (сверка имён с автосинком), test_db, test_scanner,
                     test_legacy_import, test_matching, test_dedupe, test_index_sync, test_upgrade
Dockerfile           образ: python:3.12-slim-bookworm + ffmpeg + spotdl + Deno + app
entrypoint.sh        PUID/PGID, umask 002, Deno, дроп привилегий через gosu
docker-compose.yml   стек для Portainer
.github/workflows/build.yml  сборка и публикация в GHCR
```

## Как sync совпадает с автосинком

Сервис заменяет старый bash-контейнер автосинка на тех же томах, поэтому имена и пути
считаются **идентично** ему (`naming.py`, сверено с эталонным sed в тестах):

- safe-имя: `trim_right → / \ : * ? " < > | заменить на пробел → схлопнуть пробелы → trim_right`.
  `NieR: Automata OST` → `NieR Automata OST`; кириллица и японский сохраняются.
- id плейлиста: `md5(url)[:12]` → `/conf/<id>.spotdl`.

Основа команды (плюс флаги качества и поиска из настроек — см. `jobs._common_output_args`):

```
spotdl sync <URL> --save-file /conf/<id>.spotdl --save-errors /conf/errors/<safe>.txt \
      --m3u /music/playlists/<safe>.m3u8 --cookie-file /conf/cookies.txt \
      --output "/music/spotify/{album-artist}/{album}/{track-number} - {title}.{output-ext}" \
      --format mp3
```

## Локальный запуск и тесты

```sh
python tests/test_naming.py        # или: python -m pytest tests/ -q

# локальный сервер (нужны fastapi/uvicorn и spotdl в PATH)
CONF_DIR=./_data/conf MUSIC_DIR=./_data/music APP_PASSWORD=dev AUTOSYNC_INTERVAL_HOURS=0 \
  python -m uvicorn app.main:app --reload --port 8000
```

Проверки перед коммитом:

```sh
python -c "import ast,glob;[ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('app/*.py')]"
node --check app/static/app.js
for f in $(find app/static/v2/js -name "*.js"); do node --check "$f"; done
for t in tests/test_*.py; do python "$t" || echo "FAIL $t"; done
python -c "import yaml;yaml.safe_load(open('docker-compose.yml'))"
```

Локально не проверить: реальные загрузки librespot (нужны креды, риск для аккаунта), пути
m3u (на Windows `Path` ведёт себя иначе), подпроцессы с `cwd=/app`. librespot локально может
не импортироваться из-за версии protobuf — для тестов помогает
`PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`.

## CI

[`.github/workflows/build.yml`](../.github/workflows/build.yml): push в `main`/`master` →
обычный `docker build` с inline-кэшем → публикация в GHCR. `setup-buildx-action` не
используется — падал по таймауту при тяге `moby/buildkit`. Опционально дёргает webhook
Portainer из секрета `PORTAINER_WEBHOOK`.

## Грабли

- **Не базироваться на `spotdl/spotify-downloader:latest`** — падает на старте с
  `OSError: libresolv.so.2 not found` (нужно `tls_client`). Отсюда `python:3.12-slim-bookworm`.
- **Deno — реальным файлом в `/usr/local/bin`**, не симлинком в `/root`: пользователю 998 туда
  нельзя, и spotdl считает Deno отсутствующим → `AudioProviderError` на YouTube.
- **spotdl запускается с `cwd=/`.** spotdl 4.5.0 прогоняет части пути `--m3u` через sanitize,
  который вырезает `/`, и абсолютный путь становится относительным — из `/` он снова
  указывает в `/music/...`.
- **`--save-errors` дописывает, а не перезаписывает.** Перед каждым sync errors-файл
  сбрасывается, а парсер дедуплицирует треки по Spotify-URL.
- **Не `spotdl sync saved --user-auth`.** С `--user-auth` spotdl шлёт ВСЁ через официальный
  API приложения пользователя и на каждый трек дозапрашивает трек + исполнителя + альбом
  (~3 запроса × сотни треков) — Spotify штрафует dev-приложение 429 с `Retry-After` ~сутки.
  И не список ссылок: `spotdl sync url1 url2 …` разбирает их по одной и последовательно,
  ~15–25 с на трек (часы до первой загрузки). И не пакетные `/artists` + `/albums` ради
  жанров и лейбла: на них два свежих приложения подряд получили 429 на ~21 ч. Liked Songs
  собираются только из страниц «Любимых» (`spotify_login.saved_songs`: по 50 с паузой 1 с —
  ~17 запросов на 800 треков) в файл формата spotdl, и идёт `spotdl download <файл>.spotdl`
  (`jobs.liked_args`): все поля, по которым spotdl решает «дозапросить трек» (genres,
  disc_count, tracks_count, track_number, album_id, album_artist), заполнены (genres —
  пустым списком). Полученный 429 запоминается в `/conf/.spotify-app-ratelimit.json`: пока
  штраф идёт, запросы не шлются — иначе автосинк его продлевал бы.
- **Два формата save-файла.** `sync --save-file` пишет `{"type": "sync", "songs": [...]}`,
  `download --save-file` — список. Читать через `playlists.savefile_songs` (апгрейд раньше
  понимал только список и не видел треков синкнутых плейлистов). Файл-запрос для
  `download` должен быть именно списком.
- **`COLUMNS=1000` для подпроцессов**: rich иначе рвёт строки на 80 колонок, но добивает
  их пробелами до ширины — вывод режется `rstrip()`.
- **spotipy на 429 спит весь `Retry-After`** (хоть сутки). Убрать 429 из `status_forcelist`
  мало: urllib3 повторяет ответы с `Retry-After` при любом списке, если `retries > 0`. Нужно
  `retries=0` и **непустой** список без 429 (пустой spotipy подменяет своим) — см.
  `spotify_login._client`.
- **`spotdl sync <URL> --save-file …` ничего не удаляет** — это режим «первичного» синка
  (каждый раз заново). Удаление треков, убранных из плейлиста, есть только в режиме
  `spotdl sync <file>.spotdl`, которым сервис не пользуется.
- **Код возврата spotdl ненадёжен** — он может выйти с 0 при провале загрузки. Провал
  определяется по выводу (`AudioProviderError`, `LookupError` и т.п.).
- **Флаги spotdl со списком значений** (`--audio`, `--lyrics`) идут после позиционного
  запроса — иначе съедят его.
