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

- **FastAPI + ванильный JS** (без фреймворков), один контейнер. UI — SPA в `app/static/`.
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
  auth.py            режимы входа, хэш пароля, токены сессий, анти-перебор
  config.py          пути и системные параметры из env
  settings.py        реестр настроек UI (UI → env → дефолт), секреты-файлы, cookies
  jobs.py            очередь (2 дорожки), воркеры, локи, запуск spotdl, планировщик
  naming.py          safe-имя, id плейлиста, тип ссылки — покрыто тестами
  playlists.py       playlists.txt
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
  static/            index.html, app.js, style.css
tests/test_naming.py сверка имён и id с bash/sed автосинка
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
python tests/test_naming.py
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
  Список Liked Songs забирается сами (`spotify_login.saved_track_urls`, страницы по 50),
  а spotdl получает ссылки на треки и работает своим обычным клиентом.
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
