# Самодостаточный образ: ffmpeg + spotdl + Deno + веб-приложение в одном.
# Собирается в CI (GitHub Actions) без внешних образов и публикуется в GHCR.
#
# ВАЖНО (выстрадано): НЕ использовать spotdl/spotify-downloader:latest — он падает
# на старте с `OSError: libresolv.so.2 not found` (его зависимость tls_client
# требует libresolv, которой нет в той ОС). Базируемся на python:3.12-slim-bookworm,
# где libresolv.so.2 присутствует.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Системные зависимости:
#   ffmpeg        — конвертация (spotdl без него не работает)
#   curl, unzip   — нужны для `spotdl --download-deno`
#   gosu          — дроп привилегий на PUID/PGID в entrypoint
#   tini          — корректная обработка сигналов/зомби-процессов
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates curl unzip gosu tini; \
    rm -rf /var/lib/apt/lists/*

# spotdl (не закреплён по версии — пересборка подтягивает свежий с фиксами
# YouTube/yt-dlp; при необходимости закрепите версию здесь).
RUN pip install --no-cache-dir spotdl

# Zotify — реальное аудио со Spotify (librespot). В PyPI нет, ставим из git.
# Не фатально: если репозиторий недоступен/сломан — образ всё равно соберётся,
# просто фича Zotify будет недоступна (кнопка вернёт ошибку). Для воспроизводимости
# можно закрепить на коммит (...@<sha>).
RUN pip install --no-cache-dir "git+https://github.com/zotify-dev/zotify.git" \
    || echo "[build] WARN: zotify install failed — feature will be unavailable"

# Deno обязателен: без него загрузки с YouTube падают с AudioProviderError
# ("Some YouTube downloads require Deno"). Кладём РЕАЛЬНЫЙ бинарь в /usr/local/bin
# (mode 755) — НЕ симлинк в /root, иначе под uid 998 он недоступен и spotdl
# считает deno отсутствующим.
RUN set -eux; \
    spotdl --download-deno; \
    deno_bin="$(find /root/.config/spotdl /root/.spotdl -maxdepth 1 -name 'deno' -type f 2>/dev/null | head -1)"; \
    test -n "$deno_bin"; \
    cp "$deno_bin" /usr/local/bin/deno; \
    chmod 0755 /usr/local/bin/deno; \
    spotdl --version && ffmpeg -version | head -1 && deno --version

WORKDIR /app

# Сначала зависимости приложения — лучше кэшируется
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Код приложения
COPY app /app/app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV CONF_DIR=/conf \
    MUSIC_DIR=/music \
    PUID=998 \
    PGID=100 \
    WEB_PORT=8000

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
