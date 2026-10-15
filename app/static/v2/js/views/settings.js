// Настройки: главная страница со списком разделов и подстраницы (#/settings/<раздел>).
// Обычные поля строятся из реестра настроек (/api/settings/schema): новая
// настройка в app/settings.py FIELDS появится здесь сама — в своём разделе по
// ключу или в «Прочем». Секреты и входы — особые блоки.
import { api, apiForm, signOut, storeToken } from "../api.js";
import { getTheme, setTheme } from "../main.js";
import { chip, esc, fmtAgo, icon, on, toast } from "../ui.js";

// раздел → поля реестра (ключи FIELDS)
const PAGES = [
  { label: "Источники", items: [
    { id: "spotify", icon: "brand-spotify", title: "Spotify", special: "spotify" },
    { id: "ytm", icon: "brand-youtube", title: "YouTube Music", special: "ytm" },
    { id: "deezer", icon: "music", title: "Deezer", special: "deezer" },
    { id: "librespot", icon: "player-play", title: "Spotify напрямую (320k)", special: "librespot",
      fields: ["zotify_quality", "safe_mode", "bulk_wait_min", "bulk_wait_max"] },
  ] },
  { label: "Синхронизация и загрузка", items: [
    { id: "sync", icon: "refresh", title: "Синхронизация", fields: ["sync_engine", "auto_fetch"] },
    { id: "autosync", icon: "clock", title: "Автосинк", fields: ["autosync_interval_hours", "autosync_on_start", "autosync_start_delay"] },
    { id: "youtube", icon: "download", title: "Загрузка с YouTube",
      fields: ["youtube_bitrate", "audio_providers", "overwrite", "youtube_sleep", "spotdl_threads", "download_track_timeout"] },
    { id: "lyrics", icon: "align-left", title: "Тексты песен", fields: ["lyrics_providers", "generate_lrc"] },
    { id: "files", icon: "file-music", title: "Файлы", fields: ["upload_bitrate"] },
    { id: "quality", icon: "arrow-big-up-lines", title: "Улучшение до 320k", fields: ["upgrade_min_bitrate", "upgrade_per_day", "upgrade_track_timeout"] },
  ] },
  { label: "Navidrome", items: [
    { id: "navidrome", icon: "server", title: "Navidrome", special: "navidrome" },
  ] },
  { label: "Сервис", items: [
    { id: "access", icon: "lock", title: "Доступ", special: "access", fields: ["session_ttl_days"] },
    { id: "logs", icon: "history", title: "Журнал", fields: ["jobs_history", "finished_log_lines", "verbose_spotdl_log"] },
    { id: "system", icon: "info-circle", title: "О системе", special: "system" },
  ] },
];
const ITEMS = PAGES.flatMap((g) => g.items);

let S = {};          // всё, что знаем о настройках (schema, статусы)

async function loadAll() {
  const get = (p) => api("GET", p).catch(() => null);
  const [schema, sp, ck, dz, zt, nd, auth, env] = await Promise.all([
    get("/api/settings/schema"), get("/api/spotify-app"), get("/api/cookies"), get("/api/deezer-arl"),
    get("/api/zotify-creds"), get("/api/navidrome"), get("/api/auth/status"), get("/api/status")]);
  S = { schema, sp, ck, dz, zt, nd, auth, env, fields: Object.fromEntries(((schema && schema.fields) || []).map((f) => [f.key, f])) };
  // поля, не попавшие ни в один раздел, — в «Прочее» (новая настройка не потеряется)
  const used = new Set(ITEMS.flatMap((i) => i.fields || []));
  const rest = Object.keys(S.fields).filter((k) => !used.has(k));
  const svc = PAGES[PAGES.length - 1].items;
  const other = svc.find((i) => i.id === "other");
  if (rest.length && !other) svc.push({ id: "other", icon: "adjustments", title: "Прочее", fields: rest });
  else if (other) other.fields = rest;
}

// ---------------------------------------------------------------- поля реестра
function fmtVal(f, v) {
  if (f.kind === "bool") return v ? "вкл" : "выкл";
  if (f.kind === "choice") return (f.labels && f.labels[v]) || v;
  return `${v}${f.unit ? " " + f.unit : ""}`;
}

function control(f) {
  if (f.kind === "bool") return `<button class="toggle ${f.value ? "on" : ""}" data-key="${f.key}" data-kind="bool" aria-label="${esc(f.label)}"></button>`;
  if (f.kind === "choice") {
    return `<select class="select" data-key="${f.key}" data-kind="choice">${f.choices.map((c) =>
      `<option value="${esc(c)}" ${c === f.value ? "selected" : ""}>${esc((f.labels && f.labels[c]) || c)}</option>`).join("")}</select>`;
  }
  const step = f.kind === "float" ? "any" : "1";
  return `<span class="row" style="gap:6px"><input class="field" type="number" style="width:90px" data-key="${f.key}" data-kind="${f.kind}"
    step="${step}" ${f.lo != null ? `min="${f.lo}"` : ""} ${f.hi != null ? `max="${f.hi}"` : ""} value="${esc(f.value)}">${
    f.unit ? `<span class="muted small">${esc(f.unit)}</span>` : ""}</span>`;
}

function fieldRow(key) {
  const f = S.fields[key];
  if (!f) return "";
  const def = `по умолчанию ${esc(fmtVal(f, f.default))}${f.default_source === "env" ? ` (из env ${esc(f.env)})` : ""}`;
  return `<div class="li" style="flex-wrap:wrap"><div class="grow" style="min-width:200px"><div>${esc(f.label)}</div>
      ${f.help ? `<div class="tiny muted">${esc(f.help)}</div>` : ""}
      <div class="tiny muted">${f.overridden ? `изменено · ${def} · <a href="#" data-reset="${f.key}">сбросить</a>` : def}</div></div>
    ${control(f)}</div>`;
}

const fieldsCard = (keys, label = "") => keys && keys.length
  ? `${label ? `<div class="section-label">${label}</div>` : ""}<div class="card list">${keys.map(fieldRow).join("")}</div>` : "";

// ---------------------------------------------------------------- особые разделы
const row = (ic, title, sub, right) => `<div class="li" style="flex-wrap:wrap">${icon(ic, "muted")}<div class="grow" style="min-width:180px">
  <div>${title}</div>${sub ? `<div class="tiny muted">${sub}</div>` : ""}</div>${right || ""}</div>`;

function oauthBlock(prefix, redirectHint) {
  return `<div class="hidden" id="${prefix}-steps" style="padding:12px 16px;border-top:1px solid var(--sep)">
    <ol class="small" style="margin:0 0 10px;padding-left:18px">
      <li><a id="${prefix}-link" href="#" target="_blank" rel="noopener">Открыть страницу входа Spotify ↗</a> — войдите и разрешите доступ (можно через Facebook).</li>
      <li>Браузер откроет <code>${esc(redirectHint)}</code> — страница не загрузится, это нормально. Скопируйте адрес целиком.</li>
    </ol>
    <div class="row" style="gap:8px;flex-wrap:wrap"><input class="field" id="${prefix}-redirect" style="flex:1;min-width:220px" placeholder="${esc(redirectHint)}…">
      <button class="btn primary sm" data-oauth-finish="${prefix}">Завершить вход</button></div></div>`;
}

function spotifyPage() {
  const sp = S.sp || {};
  const env = sp.source === "env";
  return `<div class="section-label">Своё приложение Spotify</div><div class="card list">
      ${env ? row("lock", "Задано в env SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET", "env важнее настроек — менять там", "")
        : `${row("key", "Client ID", "developer.spotify.com → ваше приложение → Settings",
            `<input class="field" id="sp-cid" style="width:280px;max-width:100%" value="${esc(sp.client_id || "")}" placeholder="32 символа">`)}
          ${row("lock", "Client secret", sp.has_secret ? "сохранён — оставьте пустым, чтобы не менять" : "хранится на сервере, наружу не отдаётся",
            `<input class="field" id="sp-secret" type="password" style="width:280px;max-width:100%" placeholder="${sp.has_secret ? "••••••••" : "secret"}">`)}
          <div class="li"><div class="grow tiny muted">Redirect URI в настройках приложения — ровно <code>http://127.0.0.1:9900/</code>, отмечен Web API.</div>
            ${sp.client_id ? `<button class="btn sm" data-sp-app-clear>Удалить</button>` : ""}<button class="btn primary sm" data-sp-app-save>Сохранить</button></div>`}
    </div>
    <div class="section-label">Вход (для «Любимых»)</div><div class="card list">
      ${sp.logged_in ? row("user-check", `Вы вошли${sp.user ? " как " + esc(sp.user) : ""}`, "токен обновляется сам",
          `<span class="row" style="gap:6px"><button class="btn sm" data-oauth-start="sp">Войти заново</button><button class="btn sm" data-sp-logout>Выйти</button></span>`)
        : row("user", "Вход не выполнен", sp.client_id ? "«Любимые» не синхронизируются, пока не войдёте" : "сначала сохраните приложение выше",
          sp.client_id ? `<button class="btn primary sm" data-oauth-start="sp">Войти</button>` : "")}
      ${oauthBlock("sp", "http://127.0.0.1:9900/?code=")}
    </div>`;
}

function ytmPage() {
  const ck = S.ck || {};
  const st = ck.present ? `загружены ${ck.modified ? fmtAgo(ck.modified) : ""}${ck.youtube != null ? ` · cookies youtube.com: ${ck.youtube}` : ""}` : "не загружены — YouTube может требовать «подтвердите, что вы не робот»";
  return `<div class="section-label">Cookies</div><div class="card list">
      ${row("cookie", "cookies.txt", st, `<span class="row" style="gap:6px">${ck.present ? `<button class="btn sm" data-ck-clear>Удалить</button>` : ""}
        <label class="btn ${ck.present ? "" : "primary"} sm">${icon("upload", "sm")}${ck.present ? "Загрузить новые" : "Загрузить"}<input type="file" id="ck-file" accept=".txt,text/plain" hidden></label></span>`)}
      <div class="li"><div class="grow small">
        <b style="font-weight:600">Как выгрузить, чтобы не протухли</b>
        <ol style="margin:6px 0 0;padding-left:18px" class="muted">
          <li>Откройте приватное окно (инкогнито) и войдите в YouTube.</li>
          <li>В той же вкладке откройте <code>youtube.com/robots.txt</code> — других вкладок YouTube в окне быть не должно.</li>
          <li>Экспортируйте cookies расширением «Get cookies.txt LOCALLY» (формат Netscape).</li>
          <li>Сразу закройте приватное окно — иначе браузер их сменит, и сервис увидит устаревшие.</li>
        </ol></div></div>
    </div>
    <div class="section-label">Что синкать</div><div class="card list">
      ${row("heart", "Понравившиеся и плейлисты YouTube Music", "появится на этапе 4 плана: синк, склейка с треками Spotify", chip("скоро"))}
    </div>`;
}

function deezerPage() {
  const dz = S.dz || {};
  return `<div class="section-label">Deezer</div><div class="card list">
    ${dz.source === "env" ? row("lock", "ARL задан в env DEEZER_ARL", "менять там", chip("подключён", "ok"))
      : `${row("key", "ARL-токен", dz.configured ? "сохранён — вставьте новый, чтобы заменить" : "cookie «arl» с deezer.com; бесплатный аккаунт — 128k, Premium — 320k",
          `<input class="field" id="dz-arl" type="password" style="width:280px;max-width:100%" placeholder="${dz.configured ? "••••••••" : "192 символа"}">`)}
        <div class="li"><div class="grow tiny muted">Deezer берёт треки, которых нет на YouTube, — точно по ISRC.</div>
          ${dz.configured ? `<button class="btn sm" data-dz-clear>Удалить</button>` : ""}<button class="btn primary sm" data-dz-save>Сохранить</button></div>`}
  </div>`;
}

function librespotPage(item) {
  const zt = S.zt || {};
  return `<div class="section-label">Вход</div><div class="card list">
      ${zt.configured ? row("user-check", `Вход выполнен${zt.user ? " как " + esc(zt.user) : ""}`, zt.source === "env" ? "логин и пароль из env" : "через OAuth (подходит и вход через Facebook)",
          zt.source === "env" ? "" : `<span class="row" style="gap:6px"><button class="btn sm" data-oauth-start="zt">Войти заново</button><button class="btn sm" data-zt-logout>Выйти</button></span>`)
        : row("user", "Вход не выполнен", "«Spotify 320k» и улучшение до 320k не работают", `<button class="btn primary sm" data-oauth-start="zt">Войти</button>`)}
      ${oauthBlock("zt", "http://127.0.0.1:5588/login?code=")}
    </div>${fieldsCard(item.fields, "Загрузка")}
    <p class="tiny muted" style="margin:10px 4px">Риск блокировки аккаунта Spotify не нулевой — безопасный режим и паузы его снижают.</p>`;
}

function navidromePage() {
  const nd = S.nd || {};
  const env = nd.source === "env";
  const last = nd.last_result;
  return `<div class="section-label">Подключение</div><div class="card list">
      ${row("world", "Адрес", env ? "из env NAVIDROME_URL" : "например http://navidrome:4533",
        `<input class="field" id="nd-url" style="width:260px;max-width:100%" value="${esc(nd.url || "")}" ${env ? "disabled" : ""}>`)}
      ${row("user", "Пользователь", "нужен администратор — только он может запускать скан",
        `<input class="field" id="nd-user" style="width:260px;max-width:100%" value="${esc(nd.user || "")}" ${env ? "disabled" : ""}>`)}
      ${row("lock", "Пароль", nd.has_password ? "сохранён — оставьте пустым, чтобы не менять" : "",
        `<input class="field" id="nd-pass" type="password" style="width:260px;max-width:100%" placeholder="${nd.has_password ? "••••••••" : ""}" ${env ? "disabled" : ""}>`)}
      ${row("refresh", "Пересканировать после синка и загрузок", "когда очередь затихнет",
        `<button class="toggle ${nd.enabled ? "on" : ""}" id="nd-enabled" aria-label="Пересканирование"></button>`)}
      ${row("hourglass", "Не чаще чем раз в", "", `<span class="row" style="gap:6px"><input class="field" id="nd-int" type="number" min="1" style="width:80px" value="${nd.min_interval || 15}"><span class="muted small">мин</span></span>`)}
      <div class="li" style="flex-wrap:wrap;gap:8px"><div class="grow tiny muted" id="nd-result">${last ? `${last.ok ? "последний скан" : "последняя ошибка"} ${fmtAgo(last.ts)}: ${esc(last.message)}` : ""}</div>
        ${nd.configured ? `<button class="btn sm" data-nd-test>Проверить</button><button class="btn sm" data-nd-scan>Скан сейчас</button>${env ? "" : `<button class="btn sm" data-nd-clear>Отключить</button>`}` : ""}
        <button class="btn primary sm" data-nd-save>Сохранить</button></div>
    </div>`;
}

function accessPage(item) {
  const a = S.auth || {};
  let body;
  if (!a.enabled) body = row("lock", "Вход выключен", "AUTH_ENABLED=false в compose — сервис открыт всем в сети", "");
  else if (a.source === "env") body = row("lock", "Пароль задан в env APP_PASSWORD", "менять там; смена разлогинит все устройства", "");
  else {
    body = `${row("lock", "Текущий пароль", "", `<input class="field" id="pw-cur" type="password" autocomplete="current-password" style="width:240px;max-width:100%">`)}
      ${row("lock", "Новый пароль", `минимум ${a.min_length || 6} символов; смена разлогинит остальные устройства`, `<input class="field" id="pw-new" type="password" autocomplete="new-password" style="width:240px;max-width:100%">`)}
      ${row("lock", "Ещё раз", "", `<input class="field" id="pw-new2" type="password" autocomplete="new-password" style="width:240px;max-width:100%">`)}
      <div class="li"><div class="grow"></div><button class="btn primary sm" data-pw-save>Сменить пароль</button></div>`;
  }
  return `<div class="section-label">Пароль</div><div class="card list">${body}</div>${fieldsCard(item.fields, "Сессия")}
    <div class="section-label">Это устройство</div><div class="card list">${row("logout", "Выйти", "", `<button class="btn sm" data-logout>Выйти</button>`)}</div>`;
}

function systemPage() {
  const sys = (S.schema && S.schema.system) || [];
  const env = S.env || {};
  const rows = sys.map((x) => row("point", esc(x.env), esc(x.help || ""), `<code class="small" style="word-break:break-all">${esc(x.value)}</code>`));
  if (env.spotdl_version) rows.push(row("point", "spotdl", "", `<code class="small">${esc(env.spotdl_version)}</code>`));
  rows.push(row("point", "Deno", "нужен YouTube для части роликов", env.deno ? chip("есть", "ok") : chip("нет", "warn")));
  return `<div class="section-label">Только чтение — задаётся в compose</div><div class="card list">${rows.join("")}</div>
    <div class="section-label">Прежний интерфейс</div><div class="card list">${row("external-link", "Открыть прежний интерфейс",
      "запасной вариант на время обкатки нового", `<a class="btn sm" href="/legacy">/legacy</a>`)}</div>`;
}

// ---------------------------------------------------------------- главная настроек
function status(item) {
  switch (item.id) {
    case "spotify": return S.sp && S.sp.logged_in ? ["вход выполнен", "ok"] : S.sp && S.sp.client_id ? ["нет входа", "warn"] : ["не настроено", ""];
    case "ytm": return S.ck && S.ck.present ? ["cookies есть", "ok"] : ["нет cookies", "warn"];
    case "deezer": return S.dz && S.dz.configured ? ["подключён", "ok"] : ["не настроен", ""];
    case "librespot": return S.zt && S.zt.configured ? ["вход выполнен", "ok"] : ["не настроен", ""];
    case "navidrome": return S.nd && S.nd.configured ? ["подключён", "ok"] : ["не настроен", ""];
    default: return null;
  }
}

function subtitle(item) {
  const f = (k) => S.fields[k];
  switch (item.id) {
    case "spotify": return S.sp && S.sp.user ? `вход: ${S.sp.user}` : "плейлисты и «Любимые»";
    case "ytm": return "cookies для загрузок и будущего синка";
    case "deezer": return "ARL-токен — для треков, которых нет на YouTube";
    case "librespot": return "вход через ваш аккаунт, качество, паузы";
    case "autosync": return f("autosync_interval_hours") ? (f("autosync_interval_hours").value > 0 ? `каждые ${f("autosync_interval_hours").value} ч` : "выключен") : "";
    case "youtube": return f("youtube_sleep") ? `пауза ${f("youtube_sleep").value} с · потоков ${f("spotdl_threads").value}` : "";
    case "lyrics": return f("lyrics_providers") ? fmtVal(f("lyrics_providers"), f("lyrics_providers").value) : "";
    case "files": return f("upload_bitrate") ? `mp3 ${f("upload_bitrate").value}` : "";
    case "quality": return f("upgrade_per_day") ? `лимит в сутки: ${f("upgrade_per_day").value || "нет"}` : "";
    case "navidrome": return S.nd && S.nd.url ? S.nd.url : "пересканирование после изменений";
    case "access": return S.auth && !S.auth.enabled ? "вход выключен" : "пароль, срок входа";
    case "logs": return "история задач, логи";
    case "system": return "пути, пользователь, версии";
    default: return "";
  }
}

function mainPage() {
  const t = getTheme();
  return `<h1>Настройки</h1><p class="sub">Все настройки сервиса — по разделам</p>
    <div class="section-label">Внешний вид</div><div class="card list">
      ${row("sun-moon", "Тема", "", `<div class="seg">${[["auto", "Авто"], ["light", "Светлая"], ["dark", "Тёмная"]]
        .map(([k, l]) => `<button data-theme-set="${k}" class="${t === k ? "on" : ""}">${l}</button>`).join("")}</div>`)}</div>
    ${PAGES.map((g) => `<div class="section-label">${g.label}</div><div class="card list">${g.items.map((it) => {
      const st = status(it);
      return `<a class="li click" href="#/settings/${it.id}" style="color:inherit">${icon(it.icon, "muted")}<div class="grow" style="min-width:0">
        <div>${esc(it.title)}</div><div class="tiny muted ellip">${esc(subtitle(it))}</div></div>
        ${st ? `<span class="hide-m">${chip(st[0], st[1])}</span>` : ""}${icon("chevron-right", "muted")}</a>`;
    }).join("")}</div>`).join("")}`;
}

function subPage(item) {
  let body;
  switch (item.special) {
    case "spotify": body = spotifyPage(); break;
    case "ytm": body = ytmPage(); break;
    case "deezer": body = deezerPage(); break;
    case "librespot": body = librespotPage(item); break;
    case "navidrome": body = navidromePage(); break;
    case "access": body = accessPage(item); break;
    case "system": body = systemPage(); break;
    default: body = fieldsCard(item.fields);
  }
  return `<a class="back" href="#/settings">${icon("chevron-left")}Настройки</a><h1>${esc(item.title)}</h1><div id="set-body">${body}</div>`;
}

// ---------------------------------------------------------------- экран
let currentItem = null;

export async function render(ctx) {
  await loadAll();
  currentItem = ctx.param ? ITEMS.find((i) => i.id === ctx.param) : null;
  return currentItem ? subPage(currentItem) : mainPage();
}

async function reloadPage(root) {
  await loadAll();
  const el = root.querySelector("#set-body");
  if (el && currentItem) el.outerHTML = `<div id="set-body">${subPage(currentItem).split('<div id="set-body">')[1]}`;
}

async function saveField(root, key, value) {
  try {
    await api("POST", "/api/settings", { [key]: value });
    toast("Сохранено");
  } catch (ex) { toast(ex.message); }
  await reloadPage(root);
}

const val = (root, sel) => (root.querySelector(sel) || {}).value || "";

export function mount(root) {
  on(root, "click", "[data-theme-set]", (e, b) => {
    setTheme(b.dataset.themeSet);
    root.querySelectorAll("[data-theme-set]").forEach((x) => x.classList.toggle("on", x === b));
  });
  // поля реестра
  on(root, "click", "button[data-kind=bool]", (e, b) => saveField(root, b.dataset.key, !b.classList.contains("on")));
  on(root, "change", "select[data-kind=choice]", (e, s) => saveField(root, s.dataset.key, s.value));
  on(root, "change", "input[data-kind=int], input[data-kind=float]", (e, inp) => {
    const v = inp.dataset.kind === "int" ? parseInt(inp.value, 10) : parseFloat(inp.value);
    if (Number.isNaN(v)) { toast("Нужно число"); return; }
    saveField(root, inp.dataset.key, v);
  });
  on(root, "click", "[data-reset]", (e, a) => { e.preventDefault(); saveField(root, a.dataset.reset, null); });

  // вход через Spotify (OAuth): sp — для «Любимых», zt — librespot
  const oauth = { sp: ["/api/spotify-login/start", "/api/spotify-login/finish"], zt: ["/api/zotify-login/start", "/api/zotify-login/finish"] };
  on(root, "click", "[data-oauth-start]", async (e, b) => {
    const p = b.dataset.oauthStart;
    try {
      const r = await api("POST", oauth[p][0]);
      root.querySelector(`#${p}-link`).href = r.url;
      root.querySelector(`#${p}-steps`).classList.remove("hidden");
      root.querySelector(`#${p}-link`).focus();
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-oauth-finish]", async (e, b) => {
    const p = b.dataset.oauthFinish;
    const url = val(root, `#${p}-redirect`).trim();
    if (!url) { toast("Вставьте адрес страницы, на которую вас перенаправил Spotify"); return; }
    b.disabled = true;
    try {
      const r = await api("POST", oauth[p][1], { redirect_url: url });
      toast(`Вход выполнен${r.user ? ": " + r.user : ""}${r.total != null ? ` · в «Любимых» ${r.total}` : ""}`);
      await reloadPage(root);
    } catch (ex) { toast(ex.message); }
    b.disabled = false;
  });
  on(root, "click", "[data-sp-logout]", async () => {
    if (!confirm("Выйти из Spotify? «Любимые» перестанут синхронизироваться до нового входа.")) return;
    try { await api("DELETE", "/api/spotify-login"); toast("Вы вышли из Spotify"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-sp-app-save]", async () => {
    try {
      const r = await api("POST", "/api/spotify-app", { client_id: val(root, "#sp-cid").trim(), client_secret: val(root, "#sp-secret").trim() });
      toast(r.relogin_required ? "Сохранено. Приложение сменилось — войдите заново" : "Сохранено");
      await reloadPage(root);
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-sp-app-clear]", async () => {
    if (!confirm("Удалить приложение Spotify? Вход для «Любимых» тоже удалится.")) return;
    try { await api("DELETE", "/api/spotify-app"); toast("Удалено"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-zt-logout]", async () => {
    if (!confirm("Удалить вход librespot? «Spotify 320k» и улучшение до 320k перестанут работать.")) return;
    try { await api("DELETE", "/api/zotify-creds"); toast("Вход удалён"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  // cookies
  on(root, "change", "#ck-file", async (e, inp) => {
    if (!inp.files || !inp.files[0]) return;
    const fd = new FormData();
    fd.append("file", inp.files[0]);
    try { await apiForm("/api/cookies", fd); toast("Cookies загружены"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-ck-clear]", async () => {
    if (!confirm("Удалить cookies.txt?")) return;
    try { await api("DELETE", "/api/cookies"); toast("Удалены"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  // Deezer
  on(root, "click", "[data-dz-save]", async () => {
    const arl = val(root, "#dz-arl").trim();
    if (!arl) { toast("Вставьте ARL"); return; }
    try { await api("POST", "/api/deezer-arl", { arl }); toast("Сохранено"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-dz-clear]", async () => {
    if (!confirm("Удалить ARL Deezer?")) return;
    try { await api("DELETE", "/api/deezer-arl"); toast("Удалён"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  // Navidrome
  on(root, "click", "#nd-enabled", (e, b) => b.classList.toggle("on"));
  on(root, "click", "[data-nd-save]", async () => {
    const body = { url: val(root, "#nd-url").trim(), user: val(root, "#nd-user").trim(), password: val(root, "#nd-pass"),
      enabled: root.querySelector("#nd-enabled").classList.contains("on"), min_interval: parseInt(val(root, "#nd-int"), 10) || 15 };
    try { await api("POST", "/api/navidrome", body); toast("Сохранено"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-nd-test]", async () => {
    const out = root.querySelector("#nd-result");
    out.textContent = "проверяю…";
    try {
      const r = await api("POST", "/api/navidrome/test");
      out.textContent = r.ok ? `работает · Navidrome ${r.version || ""}${r.scanning ? " · сейчас идёт скан" : ""}` : (r.message || "не работает");
    } catch (ex) { out.textContent = ex.message; }
  });
  on(root, "click", "[data-nd-scan]", async () => {
    try { await api("POST", "/api/navidrome/scan"); toast("Скан запущен"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-nd-clear]", async () => {
    if (!confirm("Отключить Navidrome? Новые треки будут появляться в нём только после его собственного скана.")) return;
    try { await api("DELETE", "/api/navidrome"); toast("Отключено"); await reloadPage(root); } catch (ex) { toast(ex.message); }
  });
  // пароль
  on(root, "click", "[data-pw-save]", async () => {
    const cur = val(root, "#pw-cur"), nw = val(root, "#pw-new"), nw2 = val(root, "#pw-new2");
    const min = (S.auth && S.auth.min_length) || 6;
    if (!cur) { toast("Введите текущий пароль"); return; }
    if (nw.length < min) { toast(`Новый пароль — минимум ${min} символов`); return; }
    if (nw !== nw2) { toast("Пароли не совпадают"); return; }
    try {
      const r = await api("POST", "/api/auth/password", { current: cur, new: nw });
      if (r.token) storeToken(r.token);          // старые сессии недействительны — эта получает новую
      toast("Пароль сменён — остальные устройства разлогинены");
      ["#pw-cur", "#pw-new", "#pw-new2"].forEach((s) => { root.querySelector(s).value = ""; });
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-logout]", () => { signOut(); toast("Вы вышли"); });
  return null;
}
