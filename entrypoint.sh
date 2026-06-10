#!/usr/bin/env sh
# Точка входа веб-UI.
#   - стартует от root, готовит окружение, затем дропает привилегии на PUID:PGID
#     (тот же uid:gid, под которым Navidrome читает музыку — по умолчанию 998:100);
#   - umask 002, чтобы файлы, создаваемые spotdl, были читаемы группой Navidrome;
#   - гарантирует, что deno виден глобально (spotdl ищет его сначала в PATH),
#     иначе загрузки с YouTube падают с AudioProviderError.
set -eu

PUID="${PUID:-998}"
PGID="${PGID:-100}"
WEB_PORT="${WEB_PORT:-8000}"
CONF_DIR="${CONF_DIR:-/conf}"
MUSIC_DIR="${MUSIC_DIR:-/music}"

# Файлы должны быть доступны на чтение/запись группе (Navidrome)
umask 0002

# --- deno: сделать реально доступным под runtime-пользователем ----------------
# spotdl: get_deno_path() -> shutil.which("deno") + проверка os.access(X_OK).
# ВАЖНО: если deno лежит симлинком в /root/.config/spotdl/deno, то под uid 998
# он НЕдоступен (в /root не пускает mode 700), и spotdl считает deno
# отсутствующим -> AudioProviderError на YouTube. Поэтому кладём РЕАЛЬНЫЙ файл
# deno в /usr/local/bin (mode 755), доступный любому пользователю.
_deno_real=""
if [ -e /usr/local/bin/deno ]; then
  _deno_real="$(readlink -f /usr/local/bin/deno 2>/dev/null || true)"
fi
if [ -z "$_deno_real" ] || [ ! -f "$_deno_real" ]; then
  for cand in /root/.config/spotdl/deno /root/.spotdl/deno \
              /usr/local/bin/deno /root/.config/spotdl/deno.exe; do
    if [ -f "$cand" ]; then _deno_real="$cand"; break; fi
  done
fi
if [ -n "$_deno_real" ] && [ -f "$_deno_real" ]; then
  # Перезаписываем /usr/local/bin/deno реальной копией (на случай, если там был
  # симлинк в /root, недоступный для 998).
  if [ "$_deno_real" != "/usr/local/bin/deno" ] || [ -L /usr/local/bin/deno ]; then
    cp -f "$_deno_real" /usr/local/bin/.deno.tmp 2>/dev/null \
      && chmod 0755 /usr/local/bin/.deno.tmp \
      && mv -f /usr/local/bin/.deno.tmp /usr/local/bin/deno
  fi
  chmod 0755 /usr/local/bin/deno 2>/dev/null || true
fi
if ! command -v deno >/dev/null 2>&1; then
  echo "[entrypoint] ВНИМАНИЕ: deno не найден. Загрузки с YouTube будут падать" \
       "с AudioProviderError. Проверьте базовый образ my-spotdl (Dockerfile.base)."
fi

# --- HOME для процесса spotdl (кэш spotify, конфиг) --------------------------
# deno глобален, поэтому HOME важен только под кэш. Делаем выделенную папку,
# доступную на запись runtime-пользователю.
APP_HOME="${APP_HOME:-/home/spotdl}"
mkdir -p "$APP_HOME"
export HOME="$APP_HOME"

# --- рабочие папки данных -----------------------------------------------------
mkdir -p "$CONF_DIR/errors" "$CONF_DIR/.webui-locks" "$CONF_DIR/.webui-uploads" \
         "$MUSIC_DIR/playlists" 2>/dev/null || true

# Чауним только то, что принадлежит сервису (HOME, локи, загрузки). НЕ трогаем
# общую музыку и чужие данные автосинка в /conf, чтобы не сломать права.
chown -R "$PUID:$PGID" "$APP_HOME" "$CONF_DIR/.webui-locks" \
         "$CONF_DIR/.webui-uploads" 2>/dev/null || true

echo "[entrypoint] запуск под $PUID:$PGID, HOME=$HOME, порт=$WEB_PORT"
echo "[entrypoint] spotdl: $(command -v spotdl || echo '?')  deno: $(command -v deno || echo 'нет')"

CMD="uvicorn app.main:app --host 0.0.0.0 --port ${WEB_PORT} --no-server-header"

# Если стартовали от root — дропаем привилегии через gosu. Иначе (контейнер уже
# запущен под нужным user:) — запускаем как есть.
if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then
  exec gosu "${PUID}:${PGID}" env HOME="$HOME" PATH="$PATH" $CMD
else
  exec $CMD
fi
