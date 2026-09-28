"use strict";
// ------------------------------------------------------------------ авторизация
const TOKEN_KEY = "spotdl_webui_token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";

// ------------------------------------------------------------------ тема оформления
// Циклично: системная → светлая → тёмная → синяя (палитра старого дизайна).
// Выбор хранится в localStorage и применяется через атрибут data-theme на <html>
// (см. style.css). «auto» = убрать атрибут → следовать prefers-color-scheme.
const THEME_KEY = "spotdl_theme";
const THEMES = [
  { id: "auto",  icon: "🌓", name: "Тема: системная" },
  { id: "light", icon: "☀️", name: "Тема: светлая" },
  { id: "dark",  icon: "🌙", name: "Тема: тёмная" },
  { id: "blue",  icon: "🌌", name: "Тема: синяя (старый дизайн)" },
];
let THEME_IDX = 0;
function applyTheme(id) {
  const root = document.documentElement;
  if (id === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", id);
  const t = THEMES.find(x => x.id === id) || THEMES[0];
  const btn = document.getElementById("theme-btn");
  if (btn) { btn.textContent = t.icon; btn.title = t.name; }
}
(function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || "auto";
  const idx = THEMES.findIndex(t => t.id === saved);
  THEME_IDX = idx >= 0 ? idx : 0;
  applyTheme(THEMES[THEME_IDX].id);
})();
document.getElementById("theme-btn").addEventListener("click", () => {
  THEME_IDX = (THEME_IDX + 1) % THEMES.length;
  const id = THEMES[THEME_IDX].id;
  localStorage.setItem(THEME_KEY, id);
  applyTheme(id);
});

function authHeaders(extra) {
  // Токен передаётся ТОЛЬКО в заголовке, никогда в URL/query.
  return Object.assign({ "Authorization": "Bearer " + TOKEN }, extra || {});
}

async function api(method, path, body) {
  const opts = { method, headers: authHeaders() };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { logout(); throw new Error("Не авторизован"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* пусто */ }
  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : ("HTTP " + res.status);
    throw new Error(msg);
  }
  return data;
}

function showApp() {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("appheader").style.display = "block";
  document.getElementById("appmain").style.display = "block";
  loadErrors();
  loadStatus();
  loadSettings();
  startTaskPolling();
}

// безопасный режим (паузы между Spotify-загрузками)
async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings");
    document.getElementById("safe-mode-toggle").checked = !!s.safe_mode;
  } catch (e) { /* игнор */ }
}
document.getElementById("safe-mode-toggle").addEventListener("change", async (e) => {
  try {
    const s = await api("POST", "/api/settings", { safe_mode: e.target.checked });
    toast(s.safe_mode ? "Безопасный режим: одиночные Spotify-загрузки в real-time" : "Безопасный режим выкл: одиночные Spotify-загрузки на полной скорости");
  } catch (err) {
    toast("Ошибка: " + err.message);
    e.target.checked = !e.target.checked;  // откатить визуально
  }
});

// ------------------------------------------------------------------ статус окружения
async function loadStatus() {
  const banner = document.getElementById("env-banner");
  try {
    const s = await api("GET", "/api/status");
    if (s.warnings && s.warnings.length) {
      banner.innerHTML = "<strong>⚠ Проверьте окружение:</strong><ul>" +
        s.warnings.map(w => "<li>" + esc(w) + "</li>").join("") + "</ul>";
      banner.style.display = "block";
    } else {
      banner.style.display = "none";
    }
  } catch (e) { banner.style.display = "none"; }
}

function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

// единый русский статус задачи (и для вкладки, и для панели справа снизу)
const STATUS_RU = { running: "идёт", done: "готово", error: "ошибка", cancelled: "отменено" };
function statusRu(status, queuePos) {
  if (status === "queued") return queuePos ? `в очереди #${queuePos}` : "в очереди";
  return STATUS_RU[status] || status;
}

// метод скачивания → цветной тег (как теги категорий на f95)
const SOURCE = {
  zotify:   { label: "Spotify 320k", cls: "src-zotify" },
  deezer:   { label: "Deezer",       cls: "src-deezer" },
  download: { label: "YouTube",      cls: "src-youtube" },
  upload:   { label: "Файл",         cls: "src-upload" },
  sync:     { label: "Синхрон",      cls: "src-sync" },
};
function _sourceTag(kind) {
  const s = SOURCE[kind];
  return s ? el("span", { class: "srctag " + s.cls }, [s.label]) : null;
}
function _taskName(j) { return j.name || j.title || ""; }
// содержимое ячейки имени: [цветной тег метода] + имя плейлиста
function _nameContent(j) {
  const tag = _sourceTag(j.kind);
  return tag ? [tag, _taskName(j)] : [_taskName(j)];
}

// допустимые источники аудио для добивания (Spotify-URL сюда НЕ годится)
function validateSource(url) {
  const u = url.trim().toLowerCase();
  if (!/^https?:\/\//.test(u)) return "не похоже на ссылку";
  if (u.includes("open.spotify.com")) return "это Spotify-ссылка — нужен источник аудио (YouTube/YT-Music/SoundCloud)";
  if (/youtube\.com|youtu\.be|soundcloud\.com|bandcamp\.com|piped/.test(u)) return null;
  return "ожидается YouTube / YT-Music / SoundCloud / Bandcamp";
}
// «Выйти» — забыть сессию на ЭТОМ устройстве и заново спросить сервер, что показать
function logout() {
  TOKEN = "";
  localStorage.removeItem(TOKEN_KEY);
  bootAuth();
}

// ------------------------------------------------------------------ утилиты
function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === "class") e.className = attrs[k];
    else if (k === "html") e.innerHTML = attrs[k];
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), attrs[k]);
    else e.setAttribute(k, attrs[k]);
  }
  (children || []).forEach(c => e.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return e;
}
let toastTimer = null;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}

// ------------------------------------------------------------------ навигация
document.querySelectorAll("nav button").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    const view = b.dataset.view;
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    document.getElementById("view-" + view).classList.add("active");
    if (view === "errors") loadErrors();
    if (view === "playlists") loadPlaylists();
    if (view === "upgrade") loadUpgrade();
    if (view === "jobs") pollTasks();
    if (view === "settings") { renderAccess(); loadSettingsPage(); loadZotify(); loadSpotifyApp(); }
  });
});

// ------------------------------------------------------------------ настройки: общие (из схемы)
// Карточки строятся из /api/settings/schema: новая настройка добавляется одной
// записью в app/settings.py FIELDS, без правок разметки. Значение из env —
// это дефолт; сохранённое в UI его переопределяет, «сбросить» возвращает дефолт.
let SETTINGS_SCHEMA = [];

function fmtSetting(f, v) {
  if (f.kind === "bool") return v ? "вкл" : "выкл";
  if (f.kind === "choice") return (f.labels && f.labels[v]) || String(v);
  return String(v) + (f.unit ? " " + f.unit : "");
}
function fmtWhen(ts) {
  if (!ts) return "";
  const sec = ts - Date.now() / 1000;
  if (sec <= 60) return "вот-вот";
  if (sec < 3600) return "через " + Math.round(sec / 60) + " мин";
  return "через " + (sec / 3600).toFixed(1).replace(/\.0$/, "") + " ч";
}

function settingControl(f) {
  let input;
  if (f.kind === "bool") {
    input = el("input", { type: "checkbox", class: "switch", "data-key": f.key });
    input.checked = !!f.value;
  } else if (f.kind === "choice") {
    input = el("select", { "data-key": f.key },
      f.choices.map(c => el("option", { value: c }, [(f.labels && f.labels[c]) || c])));
    input.value = f.value;
  } else {
    input = el("input", { type: "number", "data-key": f.key, step: f.kind === "float" ? "any" : "1" });
    if (f.lo != null) input.min = f.lo;
    if (f.hi != null) input.max = f.hi;
    input.value = f.value;
  }
  return input;
}

function renderSettingsGroups(data) {
  SETTINGS_SCHEMA = data.fields;
  const box = document.getElementById("settings-groups");
  box.innerHTML = "";
  const groups = [];
  data.fields.forEach(f => { if (!groups.includes(f.group)) groups.push(f.group); });
  groups.forEach(g => {
    const fields = data.fields.filter(f => f.group === g);
    const card = el("div", { class: "card", style: "margin-top:16px" }, [el("strong", {}, [g])]);
    if (g === "Автосинк") {
      const nxt = data.next_autosync ? "Следующий автосинк — " + fmtWhen(data.next_autosync) + "." : "Автосинк выключен.";
      card.appendChild(el("div", { class: "muted small", style: "margin-top:4px" }, [nxt]));
    }
    fields.forEach(f => {
      const control = settingControl(f);
      const def = "По умолчанию: " + fmtSetting(f, f.default) + (f.default_source === "env" ? " (из env " + f.env + ")" : "");
      const reset = el("button", { class: "linkbtn", title: "Вернуть значение по умолчанию" }, ["↺ сбросить"]);
      reset.style.visibility = f.overridden ? "visible" : "hidden";
      reset.addEventListener("click", () => saveSettings({ [f.key]: null }, card));
      card.appendChild(el("div", { class: "set-row" }, [
        el("label", { class: "set-label" }, [f.label]),
        el("div", { class: "set-control" }, [control, f.unit && f.kind !== "bool" ? el("span", { class: "muted small" }, [f.unit]) : "", reset]),
        el("div", { class: "set-help muted small" }, [f.help ? el("div", {}, [f.help]) : "", el("div", { class: "set-default" }, [def])]),
      ]));
    });
    const errEl = el("div", { class: "err-msg" });
    const save = el("button", { class: "btn" }, ["Сохранить"]);
    save.addEventListener("click", () => {
      const vals = {};
      card.querySelectorAll("[data-key]").forEach(inp => {
        const f = fields.find(x => x.key === inp.dataset.key);
        const v = f.kind === "bool" ? inp.checked : inp.value;
        if (String(v) !== String(f.value)) vals[f.key] = v;
      });
      if (!Object.keys(vals).length) { toast("Изменений нет"); return; }
      saveSettings(vals, card);
    });
    card.appendChild(errEl);
    card.appendChild(el("div", { class: "row", style: "margin-top:12px" }, [save]));
    box.appendChild(card);
  });
}

async function saveSettings(vals, card) {
  const errEl = card.querySelector(".err-msg");
  if (errEl) errEl.textContent = "";
  try {
    const s = await api("POST", "/api/settings", vals);
    document.getElementById("safe-mode-toggle").checked = !!s.safe_mode;   // тумблер в шапке
    toast("Сохранено");
    await loadSettingsPage(false);
  } catch (e) { if (errEl) errEl.textContent = e.message; else toast("Ошибка: " + e.message); }
}

function renderSystemInfo(rows) {
  const box = document.getElementById("sys-info");
  box.innerHTML = "";
  box.appendChild(el("div", { class: "sys-grid" }, rows.flatMap(r => [
    el("code", {}, [r.env]),
    el("div", {}, [el("span", {}, [r.value]), r.help ? el("div", { class: "muted" }, [r.help]) : ""]),
  ])));
}

// ------------------------------------------------------------------ настройки: YouTube cookies
function renderCookies(s) {
  const st = document.getElementById("ck-status");
  if (!s.present) {
    st.innerHTML = '<span class="muted">Файла нет</span> — YouTube может требовать вход «не робот».';
  } else {
    const when = s.modified ? new Date(s.modified * 1000).toLocaleString("ru-RU") : "?";
    st.innerHTML = `✅ Загружен ${esc(when)}` + (s.cookies != null ? ` · cookies: ${s.cookies}, из них youtube.com: ${s.youtube}` : "");
  }
  document.getElementById("ck-clear").style.display = s.present ? "" : "none";
}
document.getElementById("ck-upload").addEventListener("click", () => document.getElementById("ck-file").click());
document.getElementById("ck-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const err = document.getElementById("ck-err");
  err.textContent = "";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/cookies", { method: "POST", headers: authHeaders(), body: fd });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { logout(); return; }
    if (!res.ok) { err.textContent = data.detail || ("HTTP " + res.status); return; }
    renderCookies(data); toast("cookies.txt сохранён"); loadStatus();
  } catch (e2) { err.textContent = e2.message; }
});
document.getElementById("ck-clear").addEventListener("click", async () => {
  if (!confirm("Удалить cookies.txt?")) return;
  try { renderCookies(await api("DELETE", "/api/cookies")); toast("Удалено"); loadStatus(); }
  catch (e) { document.getElementById("ck-err").textContent = e.message; }
});

// ------------------------------------------------------------------ настройки: Deezer ARL
function renderDeezer(s) {
  const env = s.source === "env";
  document.getElementById("dz-status").innerHTML = s.configured
    ? "✅ ARL задан" + (env ? ' в env <code>DEEZER_ARL</code> — <span class="muted">он важнее UI, менять только там</span>' : "")
    : '<span class="muted">Не задан — кнопка «Deezer» у треков работать не будет.</span>';
  const inp = document.getElementById("dz-arl");
  inp.value = "";
  inp.placeholder = s.configured && !env ? "сохранён — вставьте новый, чтобы заменить" : "ARL";
  inp.disabled = env;
  document.getElementById("dz-save").disabled = env;
  document.getElementById("dz-clear").style.display = s.configured && !env ? "" : "none";
}
document.getElementById("dz-save").addEventListener("click", async () => {
  const err = document.getElementById("dz-err");
  err.textContent = "";
  try { renderDeezer(await api("POST", "/api/deezer-arl", { arl: document.getElementById("dz-arl").value })); toast("ARL сохранён"); }
  catch (e) { err.textContent = e.message; }
});
document.getElementById("dz-clear").addEventListener("click", async () => {
  if (!confirm("Удалить Deezer ARL?")) return;
  try { renderDeezer(await api("DELETE", "/api/deezer-arl")); toast("Удалено"); }
  catch (e) { document.getElementById("dz-err").textContent = e.message; }
});

// ------------------------------------------------------------------ настройки: Navidrome
function renderNavidrome(s) {
  const env = s.source === "env";
  const url = document.getElementById("nd-url"), user = document.getElementById("nd-user"),
        pw = document.getElementById("nd-password");
  url.value = s.url || ""; user.value = s.user || ""; pw.value = "";
  pw.placeholder = s.has_password ? "сохранён — оставьте пустым, чтобы не менять" : "";
  url.disabled = user.disabled = pw.disabled = env;
  document.getElementById("nd-enabled").checked = !!s.enabled;
  document.getElementById("nd-interval").value = s.min_interval;
  let st = s.configured
    ? "✅ Подключение задано" + (env ? ' в env <code>NAVIDROME_*</code> — <span class="muted">адрес и логин менять только там</span>' : "")
    : '<span class="muted">Не настроено — Navidrome увидит новые треки только по своему расписанию.</span>';
  if (s.pending) st += ' · <span class="muted">ждёт скана (после затишья в очереди)</span>';
  const r = s.last_result;
  if (r) st += `<br>${r.ok ? "Последний скан" : "⚠ Последняя попытка"}: ${esc(new Date(r.ts * 1000).toLocaleString("ru-RU"))}` +
    `${r.auto ? " (авто)" : ""} — ${esc(r.message)}`;
  document.getElementById("nd-status").innerHTML = st;
  document.getElementById("nd-test").disabled = document.getElementById("nd-scan").disabled = !s.configured;
  document.getElementById("nd-clear").style.display = s.source === "file" ? "" : "none";
}
async function ndAction(fn) {
  const err = document.getElementById("nd-err");
  err.textContent = "";
  try { await fn(); } catch (e) { err.textContent = e.message; }
}
document.getElementById("nd-save").addEventListener("click", () => ndAction(async () => {
  renderNavidrome(await api("POST", "/api/navidrome", {
    url: document.getElementById("nd-url").value, user: document.getElementById("nd-user").value,
    password: document.getElementById("nd-password").value,
    enabled: document.getElementById("nd-enabled").checked,
    min_interval: parseInt(document.getElementById("nd-interval").value, 10),
  }));
  toast("Сохранено");
}));
document.getElementById("nd-test").addEventListener("click", () => ndAction(async () => {
  const r = await api("POST", "/api/navidrome/test");
  if (!r.ok) throw new Error(r.message);
  toast(`Navidrome ${r.version || ""} на связи` + (r.count != null ? ` · в библиотеке ${r.count} треков` : "") +
        (r.scanning ? " · сейчас идёт скан" : ""));
}));
document.getElementById("nd-scan").addEventListener("click", () => ndAction(async () => {
  renderNavidrome(await api("POST", "/api/navidrome/scan"));
  toast("Скан Navidrome запущен");
}));
document.getElementById("nd-clear").addEventListener("click", () => ndAction(async () => {
  if (!confirm("Удалить подключение к Navidrome?")) return;
  renderNavidrome(await api("DELETE", "/api/navidrome"));
  toast("Удалено");
}));

// вся вкладка «Настройки» разом
async function loadSettingsPage(withSecrets = true) {
  try {
    const data = await api("GET", "/api/settings/schema");
    renderSettingsGroups(data);
    renderSystemInfo(data.system);
  } catch (e) { toast("Ошибка: " + e.message); }
  if (!withSecrets) return;
  api("GET", "/api/cookies").then(renderCookies).catch(() => {});
  api("GET", "/api/deezer-arl").then(renderDeezer).catch(() => {});
  api("GET", "/api/navidrome").then(renderNavidrome).catch(() => {});
}

// ------------------------------------------------------------------ настройки: доступ
function renderAccess() {
  const st = document.getElementById("acc-status");
  const canChange = AUTH.enabled && AUTH.source === "ui";
  if (!AUTH.enabled) {
    st.innerHTML = "🔓 Вход отключён (<code>AUTH_ENABLED=false</code>): UI открыт всем, кто достучится " +
      "до порта. Годится только для закрытой локальной сети.";
  } else if (AUTH.source === "env") {
    st.innerHTML = '🔒 Пароль задан в env (<code>APP_PASSWORD</code>/<code>APP_AUTH_TOKEN</code>) — ' +
      '<span class="muted">сменить можно только там.</span>';
  } else {
    st.innerHTML = '🔒 Пароль задан в UI. <span class="muted">Смена пароля разлогинит остальные устройства.</span>';
  }
  document.getElementById("acc-change").style.display = canChange ? "" : "none";
}
document.getElementById("acc-save").addEventListener("click", async () => {
  const cur = document.getElementById("acc-current"), nw = document.getElementById("acc-new"),
        cf = document.getElementById("acc-confirm"), err = document.getElementById("acc-err");
  err.textContent = "";
  if (nw.value.length < AUTH.min_length) { err.textContent = `Минимум ${AUTH.min_length} символов`; return; }
  if (nw.value !== cf.value) { err.textContent = "Пароли не совпадают"; return; }
  try {
    const r = await api("POST", "/api/auth/password", { current: cur.value, new: nw.value });
    TOKEN = r.token; localStorage.setItem(TOKEN_KEY, TOKEN);   // старая сессия после смены недействительна
    cur.value = nw.value = cf.value = "";
    toast("Пароль изменён");
  } catch (e) { err.textContent = e.message; }
});

// ------------------------------------------------------------------ настройки: Spotify-приложение
function renderSpotifyApp(s) {
  const id = document.getElementById("sp-client-id");
  const secret = document.getElementById("sp-client-secret");
  const env = s.source === "env";
  id.value = s.client_id || "";
  secret.value = "";   // секрет с сервера не приходит никогда
  secret.placeholder = s.has_secret ? "сохранён — оставьте пустым, чтобы не менять" : "32 символа";
  id.disabled = secret.disabled = env;
  document.getElementById("sp-save").disabled = env;
  document.getElementById("sp-clear").disabled = env || !s.source;
  const src = { env: "переменные окружения (важнее настроек UI)", file: "настройки UI", "": "не задано" }[s.source];
  document.getElementById("sp-status").innerHTML = `<span class="muted">Источник:</span> ${esc(src)}`;
  // вход имеет смысл только при заданном приложении
  document.getElementById("sp-login").style.display = s.source ? "" : "none";
  document.getElementById("sp-login-status").innerHTML = s.logged_in
    ? "✅ Вход выполнен" + (s.user ? " как <strong>" + esc(s.user) + "</strong>" : "")
    : '<span class="muted">Вход не выполнен — Liked Songs не синхронизируются.</span>';
  document.getElementById("sp-login-start").textContent = s.logged_in ? "Войти заново" : "Войти в Spotify";
  document.getElementById("sp-login-start").className = s.logged_in ? "btn secondary" : "btn";
  document.getElementById("sp-logout").style.display = s.logged_in ? "" : "none";
}
// Общий OAuth-вход «ссылка → вставить адрес 127.0.0.1:…?code= → завершить»
// (Liked Songs и librespot). Элементы по префиксу: <p>-start, -steps, -link,
// -redirect, -finish, -err. Ссылкой, а не window.open: после await всплывающее
// окно режут блокировщики.
function bindOAuthLogin(p, { startPath, finishPath, hint, onDone }) {
  const $ = s => document.getElementById(p + "-" + s);
  const hide = () => { $("steps").style.display = "none"; $("redirect").value = ""; };
  $("start").addEventListener("click", async () => {
    $("err").textContent = "";
    try {
      const r = await api("POST", startPath);
      $("link").href = r.url;
      $("steps").style.display = "";
      $("link").focus();
    } catch (e) { $("err").textContent = e.message; }
  });
  async function finish() {
    $("err").textContent = "";
    if (!$("redirect").value.trim()) { $("err").textContent = "Вставьте адрес страницы " + hint; return; }
    const btn = $("finish");
    btn.disabled = true; btn.textContent = "Проверяю…";
    try {
      const s = await api("POST", finishPath, { redirect_url: $("redirect").value });
      hide();
      onDone(s);
    } catch (e) { $("err").textContent = e.message; }
    finally { btn.disabled = false; btn.textContent = "Завершить вход"; }
  }
  $("finish").addEventListener("click", finish);
  $("redirect").addEventListener("keydown", e => { if (e.key === "Enter") finish(); });
  return { hide };
}

const SP_LOGIN = bindOAuthLogin("sp-login", {
  startPath: "/api/spotify-login/start", finishPath: "/api/spotify-login/finish",
  hint: "127.0.0.1:9900/?code=…",
  onDone: s => {
    renderSpotifyApp(s);
    toast("Вход выполнен" + (s.user ? ": " + s.user : "") +
          (s.total != null ? ` · в Liked Songs ${s.total} треков` : ""));
    loadStatus();
  },
});
document.getElementById("sp-logout").addEventListener("click", async () => {
  if (!confirm("Выйти из Spotify? Liked Songs перестанут синхронизироваться до нового входа.")) return;
  try { SP_LOGIN.hide(); renderSpotifyApp(await api("DELETE", "/api/spotify-login")); toast("Вы вышли из Spotify"); loadStatus(); }
  catch (e) { document.getElementById("sp-login-err").textContent = e.message; }
});

// ------------------------------------------------------------------ настройки: librespot / Zotify
function renderZotify(s) {
  const st = document.getElementById("zt-status");
  if (!s.configured) st.innerHTML = '<span class="muted">Вход не выполнен — «Spotify 320k» и апгрейд работать не будут.</span>';
  else if (s.source === "env") st.innerHTML = "✅ Логин/пароль заданы в env <code>ZOTIFY_USERNAME</code>/<code>ZOTIFY_PASSWORD</code>" +
    (s.user ? " (<strong>" + esc(s.user) + "</strong>)" : "") + ' — <span class="muted">менять только там.</span>';
  else st.innerHTML = "✅ Вход выполнен" + (s.user ? " как <strong>" + esc(s.user) + "</strong>" : "");
  const env = s.source === "env";
  document.getElementById("zt-login-start").style.display = env ? "none" : "";
  document.getElementById("zt-login-start").textContent = s.configured ? "Войти заново" : "Войти в Spotify";
  document.getElementById("zt-login-start").className = s.configured ? "btn secondary" : "btn";
  document.getElementById("zt-logout").style.display = s.configured && !env ? "" : "none";
}
const ZT_LOGIN = bindOAuthLogin("zt-login", {
  startPath: "/api/zotify-login/start", finishPath: "/api/zotify-login/finish",
  hint: "127.0.0.1:5588/login?code=…",
  onDone: s => { renderZotify(s); toast("Вход librespot выполнен" + (s.user ? ": " + s.user : "")); },
});
document.getElementById("zt-logout").addEventListener("click", async () => {
  if (!confirm("Удалить креды librespot? «Spotify 320k» и апгрейд перестанут работать до нового входа.")) return;
  try { ZT_LOGIN.hide(); renderZotify(await api("DELETE", "/api/zotify-creds")); toast("Креды удалены"); }
  catch (e) { document.getElementById("zt-login-err").textContent = e.message; }
});
async function loadZotify() {
  try { renderZotify(await api("GET", "/api/zotify-creds")); }
  catch (e) { document.getElementById("zt-login-err").textContent = e.message; }
}
async function loadSpotifyApp() {
  document.getElementById("sp-err").textContent = "";
  try { renderSpotifyApp(await api("GET", "/api/spotify-app")); }
  catch (e) { document.getElementById("sp-err").textContent = e.message; }
}
document.getElementById("sp-save").addEventListener("click", async () => {
  const errEl = document.getElementById("sp-err");
  errEl.textContent = "";
  try {
    const s = await api("POST", "/api/spotify-app", {
      client_id: document.getElementById("sp-client-id").value,
      client_secret: document.getElementById("sp-client-secret").value,
    });
    renderSpotifyApp(s);
    toast(s.relogin_required ? "Сохранено. Приложение сменилось — войдите в Spotify заново" : "Сохранено");
    loadStatus();
  } catch (e) { errEl.textContent = e.message; }
});
document.getElementById("sp-clear").addEventListener("click", async () => {
  if (!confirm("Удалить Spotify-приложение и токен входа? Liked Songs перестанут синхронизироваться.")) return;
  try { renderSpotifyApp(await api("DELETE", "/api/spotify-app")); toast("Удалено"); loadStatus(); }
  catch (e) { document.getElementById("sp-err").textContent = e.message; }
});

// ------------------------------------------------------------------ ненайденные
let ERROR_GROUPS = [];
// ROWS: все строки треков с привязкой к DOM, чтобы убирать/помечать их по факту
// завершения задач (без перерисовки всей таблицы).
let ROWS = [];          // {spotify_url, safe, input, statusEl, rowEl, nameCell, ytLink, song}
// PENDING: job.id -> ROW, для удаления строки по завершении её задачи скачивания
let PENDING = new Map();
// RESOLVED: spotify_url -> настоящее имя (дорезолвленное со Spotify для битых, напр. musicShelfRenderer)
let RESOLVED = {};

// Имя считается «битым», если пустое, содержит внутренние ключи YTM или не имеет
// разделителя «Артист - Название» (spotdl-имя всегда вида "Artist - Title").
function badName(s) {
  if (!s) return true;
  if (/Renderer\b|musicShelf|ResponsiveListItem|sectionList/i.test(s)) return true;
  return !s.includes(" - ");
}

const ERR_LABELS = {
  LookupError: ["не найден", "spotdl не нашёл трек на YouTube — найдите ссылку сами или возьмите другой источник"],
  AudioProviderError: ["YouTube не отдал", "Трек найден, но yt-dlp его не скачал (обычно лимит YouTube или устаревшие cookies). " +
    "Ссылка уже подставлена: «Скачать» повторит загрузку; следующий синк тоже попробует снова."],
};
function errTag(type) {
  const [text, title] = ERR_LABELS[type] || [type || "?", ""];
  return el("span", { class: "tag err", title: title || type || "" }, [text]);
}

async function resolveBadNames(rows) {
  const bad = rows.filter(r => badName(r.song) && !RESOLVED[r.spotify_url]);
  if (!bad.length) return;
  const urls = [...new Set(bad.map(r => r.spotify_url))];
  let names;
  try { names = await api("POST", "/api/resolve-names", { urls }); }
  catch (e) { return; }
  rows.forEach(r => {
    const nm = names[r.spotify_url];
    if (!nm) return;
    RESOLVED[r.spotify_url] = nm;
    r.song = nm;
    r.nameCell.textContent = nm;
    r.ytLink.href = "https://music.youtube.com/search?q=" + encodeURIComponent(nm);
  });
}

async function loadErrors() {
  try {
    const data = await api("GET", "/api/errors");
    ERROR_GROUPS = data.groups || [];
    const sel = document.getElementById("err-playlist-filter");
    const cur = sel.value;
    sel.innerHTML = '<option value="">Все плейлисты</option>';
    ERROR_GROUPS.forEach(g => sel.appendChild(el("option", { value: g.safe }, [g.safe])));
    sel.value = cur;
    renderErrors();
  } catch (e) { toast("Ошибка: " + e.message); }
}

function renderErrors() {
  const cont = document.getElementById("errors-container");
  cont.innerHTML = "";
  ROWS = [];
  const filter = document.getElementById("err-filter").value.toLowerCase();
  const plFilter = document.getElementById("err-playlist-filter").value;
  let total = 0;

  ERROR_GROUPS.forEach(g => {
    if (plFilter && g.safe !== plFilter) return;
    const tracks = g.tracks.filter(t => {
      if (!filter) return true;
      return (g.safe + " " + (t.song || "") + " " + (t.error_type || "")).toLowerCase().includes(filter);
    });
    if (!tracks.length) return;
    total += tracks.length;

    const tbody = el("tbody");
    const groupRows = [];
    tracks.forEach(t => {
      const input = el("input", { type: "text", class: "yt-input", placeholder: "YouTube / YT-Music URL" });
      // трек найден, но yt-dlp его не скачал — ссылка уже есть, «Скачать» повторит
      if (t.source_url) input.value = t.source_url;
      const dlBtn = el("button", { class: "btn small" }, ["Скачать"]);
      const dzBtn = el("button", { class: "btn secondary small", title: "Скачать с Deezer по ISRC (нужен ARL) — для треков, которых нет на YouTube" }, ["Deezer"]);
      const ztBtn = el("button", { class: "btn secondary small", title: "Скачать напрямую со Spotify (librespot, 320k с Premium). Нужны креды Spotify." }, ["Spotify 320k"]);
      const fileInput = el("input", { type: "file", accept: "audio/*,.mp3,.flac,.m4a,.opus,.ogg,.wav", style: "display:none" });
      const fileBtn = el("button", { class: "btn secondary small", title: "Залить локальный файл — получит мету и обложку со Spotify" }, ["📁 Файл"]);
      const displayName = RESOLVED[t.spotify_url] || t.song || "";
      const nameCell = el("td", { class: "err-name" }, [displayName || el("span", { class: "muted" }, ["(имя не распознано)"])]);
      const ytLink = el("a", { href: "https://music.youtube.com/search?q=" + encodeURIComponent(displayName), target: "_blank", rel: "noopener" }, ["искать на YT ↗"]);
      // блок кнопок-источников и блок статуса (показывается ВМЕСТО кнопок, пока идёт задача)
      const buttonsWrap = el("span", { class: "err-buttons" }, [dlBtn, dzBtn, ztBtn, fileBtn, fileInput]);
      const statusWrap = el("span", { class: "err-status", style: "display:none" });
      const rowEl = el("tr", {}, [
        nameCell,
        el("td", {}, [errTag(t.error_type)]),
        el("td", {}, [el("a", { href: t.spotify_url, target: "_blank", rel: "noopener" }, ["Spotify ↗"])]),
        el("td", {}, [ytLink]),
        el("td", {}, [input]),
        el("td", { class: "err-actions" }, [buttonsWrap, statusWrap]),
      ]);
      const row = { spotify_url: t.spotify_url, safe: g.safe, input, rowEl, nameCell, ytLink, song: displayName, buttonsWrap, statusWrap, jobId: null };
      ROWS.push(row); groupRows.push(row);
      dlBtn.addEventListener("click", () => enqueueDownloads([row]));
      dzBtn.addEventListener("click", () => tryDeezer(row));
      ztBtn.addEventListener("click", () => trySource(row, "/api/zotify", "Spotify 320k"));
      fileBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files[0]) uploadFile(row, fileInput.files[0]);
        fileInput.value = "";
      });
      input.addEventListener("input", () => input.classList.remove("input-bad"));  // сбросить подсветку неверной ссылки
      tbody.appendChild(rowEl);
    });

    const dlGroupBtn = el("button", { class: "btn", title: "Скачать все треки этого плейлиста выбранным сверху источником" }, ["Скачать все"]);
    dlGroupBtn.addEventListener("click", () => bulkDownload(groupRows));

    cont.appendChild(el("div", { class: "card" }, [
      el("div", { class: "row", style: "justify-content:space-between;margin-bottom:10px" }, [
        el("div", {}, [
          el("strong", {}, [g.safe]),
          el("span", { class: "muted small" }, ["  " + tracks.length + " трек(ов)" + (g.timestamp ? " • " + g.timestamp : "")]),
        ]),
        el("div", { class: "row" }, [dlGroupBtn]),
      ]),
      el("table", {}, [
        el("thead", {}, [el("tr", {}, [
          el("th", {}, ["Artist - Title"]), el("th", {}, ["Ошибка"]),
          el("th", {}, ["Spotify"]), el("th", {}, ["Поиск"]),
          el("th", {}, ["Источник YouTube"]), el("th", {}, ["Действие"]),
        ])]),
        tbody,
      ]),
    ]));
  });

  if (!total) {
    cont.appendChild(el("div", { class: "card muted" }, ["Ненайденных треков нет 🎉 (или файлы errors/*.txt пусты)"]));
  }
  const grand = ERROR_GROUPS.reduce((n, g) => n + g.tracks.length, 0);
  document.getElementById("err-total").textContent =
    grand ? `всего не скачано: ${grand}` + (total !== grand ? ` (показано: ${total})` : "") : "";

  // дорезолвить настоящие имена для строк с битым именем (musicShelfRenderer и т.п.)
  resolveBadNames(ROWS);
}

// Состояние строки: показываем ЛИБО кнопки-источники (idle), ЛИБО компактный статус
// (queued/running/error) ВМЕСТО них. Детали и лог — в панели «Задачи».
// opts: { jobId — для кнопки отмены, pos — позиция в очереди }.
function setRowState(row, state, opts) {
  opts = opts || {};
  const sw = row.statusWrap;
  sw.innerHTML = "";
  if (state === "idle") {
    row.rowEl.classList.remove("row-active", "row-error");
    row.buttonsWrap.style.display = "";
    sw.style.display = "none";
    row.input.disabled = false;
    return;
  }
  row.buttonsWrap.style.display = "none";
  sw.style.display = "inline-flex";

  if (state === "queued" || state === "running") {
    row.rowEl.classList.add("row-active");
    row.rowEl.classList.remove("row-error");
    row.input.disabled = true;
    sw.appendChild(el("span", { class: "st-spin" }));
    sw.appendChild(el("span", { class: "st-label" }, [
      state === "running" ? "Идёт…" : (opts.pos ? `В очереди #${opts.pos}` : "В очереди"),
    ]));
    if (opts.jobId != null) {
      const cancel = el("button", { class: "st-act", title: "Отменить" }, ["✕"]);
      cancel.addEventListener("click", () => cancelJob(opts.jobId));
      sw.appendChild(cancel);
    }
  } else if (state === "error") {
    row.rowEl.classList.add("row-error");
    row.rowEl.classList.remove("row-active");
    row.input.disabled = false;
    sw.appendChild(el("span", { class: "st-icon err" }, ["⚠"]));
    sw.appendChild(el("span", { class: "st-label err" }, ["Ошибка"]));
    const retry = el("button", { class: "st-act retry", title: "Вернуть кнопки и попробовать снова" }, ["Повторить"]);
    retry.addEventListener("click", () => setRowState(row, "idle"));
    sw.appendChild(retry);
  }
}

// Поставить в очередь скачивание для набора строк (1 или много) — НЕ блокирует.
async function enqueueDownloads(rows) {
  rows = (rows || []).filter(r => r.input.value.trim());
  if (!rows.length) { toast("Заполните поле источника (YouTube/YT-Music URL)"); return; }
  // валидация ссылок-источников: невалидные подсвечиваем и не отправляем
  const bad = rows.filter(r => validateSource(r.input.value));
  if (bad.length) {
    bad.forEach(r => {
      r.input.classList.add("input-bad");
      r.input.title = validateSource(r.input.value);  // подсветить поле + подсказка, кнопки не трогаем
    });
    rows = rows.filter(r => !validateSource(r.input.value));
    if (!rows.length) { toast("Ссылка-источник некорректна"); return; }
    toast(`Пропущено некорректных ссылок: ${bad.length}`);
  }
  const items = rows.map(r => ({
    spotify_url: r.spotify_url, youtube_url: r.input.value.trim(), safe: r.safe,
  }));
  try {
    const data = await api("POST", "/api/download-batch", { items });
    // привязываем созданные задачи скачивания к строкам по spotify_url
    (data.downloads || []).forEach(d => {
      const row = rows.find(r => r.spotify_url === d.spotify_url);
      if (row) {
        row.jobId = d.id;
        PENDING.set(d.id, row);
        setRowState(row, "queued", { jobId: d.id });
      }
    });
    toast(`В очередь: ${(data.downloads || []).length} трек(ов)` +
          ((data.syncs || []).length ? `, обновление m3u: ${data.syncs.length}` : ""));

    pollTasks();
  } catch (e) { toast("Ошибка: " + e.message); }
}

// Попытка скачать трек из альтернативного источника (Deezer/Spotify).
// quiet=true (для массовой) подавляет тост/опрос на каждую строку — их делают раз.
async function trySource(row, endpoint, label, extra, quiet) {
  setRowState(row, "queued");  // ещё без jobId — кнопка отмены появится после ответа
  try {
    const body = Object.assign({ spotify_url: row.spotify_url, safe: row.safe }, extra || {});
    const data = await api("POST", endpoint, body);
    row.jobId = data.job.id;
    PENDING.set(data.job.id, row);
    setRowState(row, "queued", { jobId: data.job.id });
    if (!quiet) { toast(`Попытка ${label} в очереди`); pollTasks(); }
  } catch (e) {
    setRowState(row, "error");
    if (!quiet) toast(`${label}: ${e.message}`);
  }
}
function tryDeezer(row) { return trySource(row, "/api/deezer", "Deezer"); }

// Залить локальный файл: получит мету/обложку со Spotify и ляжет в библиотеку.
async function uploadFile(row, file) {
  const fd = new FormData();
  fd.append("spotify_url", row.spotify_url);
  fd.append("safe", row.safe);
  fd.append("file", file);
  setRowState(row, "queued");
  try {
    // НЕ задаём Content-Type — браузер сам выставит multipart boundary.
    const res = await fetch("/api/upload", { method: "POST", headers: authHeaders(), body: fd });
    if (res.status === 401) { logout(); return; }
    let data = null; try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error((data && data.detail) || ("HTTP " + res.status));
    row.jobId = data.job.id;
    PENDING.set(data.job.id, row);
    setRowState(row, "queued", { jobId: data.job.id });
    toast("Файл «" + file.name + "» в очереди");
    pollTasks();
  } catch (e) {
    setRowState(row, "error");
    toast("Ошибка заливки: " + e.message);
  }
}

document.getElementById("err-filter").addEventListener("input", renderErrors);
document.getElementById("err-playlist-filter").addEventListener("change", renderErrors);
document.getElementById("err-reload").addEventListener("click", () => { loadErrors(); loadStatus(); });
document.getElementById("err-download-all").addEventListener("click", () => bulkDownload(ROWS));

// Массовое скачивание выбранным источником (dropdown #dl-mode).
function bulkDownload(rows) {
  rows = rows || [];
  const mode = document.getElementById("dl-mode").value;
  if (mode === "url") {
    enqueueDownloads(rows.filter(r => r.input.value.trim()));
    return;
  }
  const endpoint = mode === "deezer" ? "/api/deezer" : "/api/zotify";
  const label = mode === "deezer" ? "Deezer" : "Spotify 320k";
  if (!rows.length) { toast("Нет треков"); return; }
  const note = mode === "spotify" ? " (real-time, медленно — безопасно от бана)" : "";
  if (!confirm(`Поставить в очередь ${rows.length} трек(ов) через ${label}?${note}`)) return;
  // массовая Spotify-закачка → всегда real-time (bulk:true)
  const extra = mode === "spotify" ? { bulk: true } : {};
  rows.forEach(r => trySource(r, endpoint, label, extra, true));  // quiet — один тост ниже
  toast(`В очередь: ${rows.length} трек(ов) через ${label}`);
  pollTasks();
}

// ------------------------------------------------------------------ плейлисты (карточки)
// Карточка = плейлист + его состояние: идёт синк (прогресс), в очереди, сколько
// треков, сколько не найдено (ссылка сразу на добивание), когда синкался.
// Живой статус приходит из общего опроса (/api/jobs → syncs), сводка — из /api/playlists.
let EDIT_URL = null;
let PLAYLISTS = [];
const PL_NODES = new Map();   // playlist_id -> {card, chip, live, meta, syncBtn}

const PL_ICON = { saved: "♥", playlist: "♫", album: "◉", artist: "★", track: "♪" };

async function loadPlaylists() {
  try {
    const data = await api("GET", "/api/playlists");
    PLAYLISTS = data.playlists || [];
    renderPlaylistCards();
  } catch (e) { toast("Ошибка: " + e.message); }
}

function renderPlaylistCards() {
  const box = document.getElementById("pl-cards");
  box.innerHTML = "";
  PL_NODES.clear();
  if (!PLAYLISTS.length) {
    box.appendChild(el("div", { class: "card empty-state" }, [
      el("strong", {}, ["Добавьте первый плейлист"]),
      el("div", { class: "muted small", style: "margin-top:4px" },
        ["Ссылка на плейлист, альбом или исполнителя Spotify — или saved для «Любимых треков»."]),
    ]));
    return;
  }
  PLAYLISTS.forEach(p => {
    const chip = el("span", { class: "chip" }, [""]);
    const syncBtn = el("button", { class: "btn small" }, ["⟳ Синк"]);
    syncBtn.addEventListener("click", () => syncPlaylist(p, syncBtn));
    const editBtn = el("button", { class: "btn secondary small", title: "Изменить" }, ["✎"]);
    editBtn.addEventListener("click", () => openPlModal(p));
    const delBtn = el("button", { class: "btn secondary small", title: "Удалить из списка" }, ["🗑"]);
    delBtn.addEventListener("click", () => deletePlaylist(p));
    // у Liked Songs (`saved`) ссылки нет — ведём на коллекцию в веб-плеере
    const href = p.type === "saved" ? "https://open.spotify.com/collection/tracks" : p.url;
    const name = el("a", { class: "pl-name", href, target: "_blank", rel: "noopener", title: "Открыть в Spotify" }, [p.name]);
    const live = el("div", { class: "pl-live" });
    const meta = el("div", { class: "pl-meta muted small" });
    const card = el("div", { class: "card pl-card" }, [
      el("div", { class: "pl-head" }, [
        el("span", { class: "pl-icon", "aria-hidden": "true" }, [PL_ICON[p.type] || "♫"]),
        name, chip,
        el("div", { class: "pl-actions" }, [syncBtn, editBtn, delBtn]),
      ]),
      live, meta,
    ]);
    box.appendChild(card);
    PL_NODES.set(p.id, { card, chip, live, meta, syncBtn, p });
  });
  updatePlaylistLive(LIVE_SYNCS);
}

// Живое состояние карточек (вызывается на каждом опросе).
function updatePlaylistLive(syncs) {
  for (const [id, n] of PL_NODES) {
    const s = syncs[id];
    const p = n.p;
    const prog = s && s.progress;
    let chipText = "", chipCls = "";
    if (s && s.status === "running") {
      const pct = prog && prog.total && prog.phase === "download" ? Math.round(prog.processed / prog.total * 100) : null;
      chipText = pct != null ? `синк ${pct}%` : "синк…";
      chipCls = "accent";
    } else if (s && s.status === "queued") {
      chipText = "в очереди"; chipCls = "muted";
    } else if (p.not_found) {
      chipText = `${p.not_found} не скачано`; chipCls = "warn";
    } else if (p.last_sync) {
      chipText = p.last_sync.ok ? "готово" : "ошибка синка"; chipCls = p.last_sync.ok ? "ok" : "danger";
    } else {
      chipText = "не синхронизирован"; chipCls = "muted";
    }
    n.chip.textContent = chipText;
    n.chip.className = "chip " + chipCls;
    n.syncBtn.disabled = !!s;
    n.card.classList.toggle("syncing", !!(s && s.status === "running"));

    // строка прогресса — только пока идёт синк этого плейлиста
    n.live.innerHTML = "";
    if (s && s.status === "running" && prog) {
      n.live.appendChild(progressBar(prog.phase === "download" ? prog.processed : null, prog.total));
      n.live.appendChild(el("div", { class: "muted small" }, [syncDetail(prog)]));
    }

    // сводка
    n.meta.innerHTML = "";
    const parts = [];
    if (p.tracks != null) parts.push(`${p.tracks} ${plural(p.tracks, "трек", "трека", "треков")}`);
    if (p.last_sync) {
      let t = "синк " + fmtAgo(p.last_sync.ts);
      if (p.last_sync.downloaded) t += `, +${p.last_sync.downloaded}`;
      parts.push(t);
      if (p.last_sync.yt_blocked) parts.push("⚠ " + YT_BLOCK_HINT);
    } else if (p.tracks == null && !(s && s.status === "running")) {
      parts.push("ещё не синхронизировался");
    }
    n.meta.appendChild(document.createTextNode(parts.join(" · ")));
    if (p.not_found) {
      const a = el("a", { href: "#" }, [`добить ${p.not_found} →`]);
      a.addEventListener("click", (e) => { e.preventDefault(); openErrorsFor(p.safe); });
      if (parts.length) n.meta.appendChild(document.createTextNode(" · "));
      n.meta.appendChild(a);
    }
  }
}

async function openErrorsFor(safe) {
  switchView("errors");
  await loadErrors();
  const sel = document.getElementById("err-playlist-filter");
  sel.value = safe;
  renderErrors();
}

async function syncPlaylist(p, btn) {
  btn.disabled = true;
  try { await api("POST", "/api/sync", { url: p.url }); toast("Синхронизация в очереди"); pollTasks(); }
  catch (e) { toast("Ошибка: " + e.message); btn.disabled = false; }
}

async function deletePlaylist(p) {
  if (!confirm("Удалить «" + p.name + "» из playlists.txt?")) return;
  try { await api("DELETE", "/api/playlists", { url: p.url }); loadPlaylists(); toast("Удалено"); }
  catch (e) { toast("Ошибка: " + e.message); }
}
function openPlModal(p) {
  EDIT_URL = p ? p.url : null;
  document.getElementById("pl-modal-title").textContent = p ? "Редактировать плейлист" : "Добавить плейлист";
  document.getElementById("pl-modal-name").value = p ? p.name : "";
  document.getElementById("pl-modal-url").value = p ? p.url : "";
  document.getElementById("pl-modal-err").textContent = "";
  document.getElementById("pl-modal-bg").classList.add("open");
}
function closePlModal() { document.getElementById("pl-modal-bg").classList.remove("open"); }
document.getElementById("pl-add-btn").addEventListener("click", () => openPlModal(null));
document.getElementById("pl-reload").addEventListener("click", loadPlaylists);
document.getElementById("pl-modal-cancel").addEventListener("click", closePlModal);
document.getElementById("pl-modal-save").addEventListener("click", async () => {
  const name = document.getElementById("pl-modal-name").value;
  const url = document.getElementById("pl-modal-url").value;
  const errEl = document.getElementById("pl-modal-err");
  errEl.textContent = "";
  try {
    if (EDIT_URL) await api("PUT", "/api/playlists", { old_url: EDIT_URL, name, url });
    else await api("POST", "/api/playlists", { name, url });
    closePlModal(); loadPlaylists(); toast("Сохранено");
  } catch (e) { errEl.textContent = e.message; }
});

// ------------------------------------------------------------------ апгрейд 320k
let UPG_TIMER = null;
function fmtEta(sec) {
  if (sec == null) return "—";
  if (sec < 3600) return Math.round(sec / 60) + " мин";
  if (sec < 86400) return (sec / 3600).toFixed(1) + " ч";
  return (sec / 86400).toFixed(1) + " дн";
}
async function loadUpgrade() {
  clearTimeout(UPG_TIMER);
  let s;
  try { s = await api("GET", "/api/upgrade/status"); }
  catch (e) { document.getElementById("upg-progress").textContent = "Ошибка: " + e.message; return; }
  renderUpgrade(s);
  if (["running", "stopping", "waiting"].includes(s.state) && !document.hidden &&
      document.getElementById("view-upgrade").classList.contains("active")) {
    UPG_TIMER = setTimeout(loadUpgrade, 2000);
  }
}
function renderUpgrade(s) {
  const cont = document.getElementById("upg-progress");
  const processed = s.done + s.failed + s.skipped;
  const pct = s.total ? Math.round(processed / s.total * 100) : 0;
  const stateLabel = { idle: "ожидание", running: "идёт", stopping: "останавливается…", stopped: "остановлен", waiting: "пауза (дневной лимит)" }[s.state] || s.state;
  cont.innerHTML = "";
  cont.appendChild(el("div", { class: "row", style: "gap:18px;flex-wrap:wrap;margin-bottom:8px" }, [
    el("span", {}, [el("span", { class: "muted" }, ["Статус: "]), el("strong", {}, [stateLabel])]),
    el("span", {}, [el("span", { class: "muted" }, ["Улучшено всего: "]), el("strong", {}, [String(s.upgraded_total)])]),
  ]));
  const fill = el("div", { style: `height:100%;width:${pct}%;background:var(--accent);transition:width .3s` }, []);
  cont.appendChild(el("div", { style: "background:var(--bg);border:1px solid var(--border);border-radius:6px;height:14px;overflow:hidden;margin:8px 0" }, [fill]));
  cont.appendChild(el("div", { class: "row small muted", style: "gap:18px;flex-wrap:wrap" }, [
    el("span", {}, [`${processed} / ${s.total} (${pct}%)`]),
    el("span", {}, [`готово: ${s.done}`]),
    el("span", {}, [`пропущено: ${s.skipped}`]),
    el("span", {}, [`ошибок: ${s.failed}`]),
    el("span", {}, [`осталось ~${fmtEta(s.eta_seconds)}`]),
  ]));
  if (s.current) cont.appendChild(el("div", { class: "small", style: "margin-top:8px" }, [el("span", { class: "muted" }, ["Сейчас: "]), s.current]));
}
document.getElementById("upg-start").addEventListener("click", async () => {
  if (!confirm("Запустить апгрейд всей библиотеки до 320k? Идёт медленно (дни), можно остановить в любой момент.")) return;
  try { await api("POST", "/api/upgrade/start"); toast("Апгрейд запущен"); loadUpgrade(); }
  catch (e) { toast("Ошибка: " + e.message); }
});
document.getElementById("upg-stop").addEventListener("click", async () => {
  try { await api("POST", "/api/upgrade/stop"); toast("Останавливаю…"); loadUpgrade(); }
  catch (e) { toast("Ошибка: " + e.message); }
});

document.getElementById("syncall-btn").addEventListener("click", async () => {
  if (!confirm("Запустить синхронизацию всех плейлистов? Это может занять много времени.")) return;
  try { await api("POST", "/api/sync-all"); toast("Синхронизация всех в очереди"); pollTasks(); }
  catch (e) { toast("Ошибка: " + e.message); }
});

// ------------------------------------------------------------------ активность, строка статуса, опрос
// Главный индикатор — строка статуса вверху (видна на любой вкладке): что сервис
// делает прямо сейчас, с прогрессом. Вкладка «Активность» — понятные строки задач,
// сырой лог — по клику. Всё питается одним опросом /api/jobs.
let TASK_POLL_TIMER = null;
let TASK_POLL_PAUSED = false;   // опрос остановлен, т.к. вкладка не видна
let SEL_JOB = null;             // id задачи, чей лог открыт (под строкой)
let SEL_LOG_OFFSET = 0;
const LOG_EL = document.getElementById("tasklog");
let LIVE_SYNCS = {};            // playlist_id -> {status, job_id, progress}

function hideLog() { SEL_JOB = null; if (LOG_EL.parentNode) LOG_EL.remove(); LOG_EL.style.display = "none"; }

// ----- форматирование
function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}
function fmtAgo(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "только что";
  if (s < 3600) return Math.round(s / 60) + " мин назад";
  if (s < 86400) return Math.round(s / 3600) + " ч назад";
  return Math.round(s / 86400) + " дн назад";
}
function fmtLeft(sec) {
  if (sec == null) return "";
  if (sec < 90) return "осталось ~1 мин";
  if (sec < 3600) return `осталось ~${Math.round(sec / 60)} мин`;
  if (sec >= 48 * 3600) return `осталось ~${Math.round(sec / 86400)} дн`;
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return `осталось ~${h} ч` + (m ? ` ${m} мин` : "");
}
// полоса прогресса; done == null — неопределённая (идёт подготовка)
function progressBar(done, total) {
  const bar = el("div", { class: "pbar" + (done == null || !total ? " indeterminate" : "") });
  const fill = el("i");
  if (done != null && total) fill.style.width = Math.min(100, done / total * 100).toFixed(1) + "%";
  bar.appendChild(fill);
  return bar;
}
const PHASE_TEXT = {
  list: "получаю список «Любимых» из Spotify…",
  prepare: "собираю данные треков…",
};
// Неудачи синка по сортам: «не найдено» — трека нет на YouTube (добивать
// вручную), «YouTube не отдал» — найден, но не скачан (лимит YouTube / cookies:
// лечится повтором синка). Старые итоги без разбивки — одним числом.
function failParts(p) {
  if (p.lookup_failed == null) return p.failed ? [`не удалось ${p.failed}`] : [];
  const out = [];
  if (p.lookup_failed) out.push(`не найдено ${p.lookup_failed}`);
  if (p.download_failed) out.push(`YouTube не отдал ${p.download_failed}`);
  const other = p.failed - p.lookup_failed - p.download_failed;
  if (other > 0) out.push(`ошибок ${other}`);
  return out;
}
const YT_BLOCK_HINT = "YouTube перестал отдавать аудио — повторите синк через несколько часов";

function syncDetail(p) {
  if (p.phase === "list") return PHASE_TEXT.list;
  if (p.phase === "prepare") return PHASE_TEXT.prepare + (p.total ? ` ${p.total} ${plural(p.total, "трек", "трека", "треков")}` : "");
  const parts = [`${p.processed} из ${p.total}`];
  if (p.downloaded) parts.push(`скачано ${p.downloaded}`);
  if (p.skipped) parts.push(`уже было ${p.skipped}`);
  parts.push(...failParts(p));
  if (p.eta_seconds != null) parts.push(fmtLeft(p.eta_seconds));
  return parts.join(" · ");
}
function syncSummary(p) {
  const parts = [];
  if (p.downloaded) parts.push(`скачано ${p.downloaded}`);
  if (p.skipped) parts.push(`уже было ${p.skipped}`);
  parts.push(...failParts(p));
  if (p.yt_blocked) parts.push(YT_BLOCK_HINT);
  return parts.length ? parts.join(" · ") : (p.total ? "новых треков нет" : "");
}

// ----- переход на вкладку
function switchView(view) {
  const b = document.querySelector(`nav button[data-view="${view}"]`);
  if (b) b.click();
}
function openJobLog(jobId) {
  switchView("jobs");
  if (SEL_JOB !== jobId) selectTask(jobId);
}

// ----- строка статуса
const KIND_TITLE = { sync: "Синхронизация", "sync-all": "Синхронизация всех", autosync: "Автосинхронизация" };

function stripItem({ icon, iconCls, title, sub, right, bar, extra, onClick }) {
  const item = el("div", { class: "strip-item" + (onClick ? " clickable" : "") }, [
    el("div", { class: "strip-head" }, [
      el("span", { class: "strip-icon " + (iconCls || ""), "aria-hidden": "true" }, [icon]),
      el("div", { class: "strip-text" }, [
        el("div", { class: "strip-title" }, [title]),
        sub ? el("div", { class: "strip-sub muted small" }, [sub]) : "",
      ]),
      right ? el("div", { class: "strip-right" }, right) : "",
    ]),
    bar || "",
    extra ? el("div", { class: "strip-extra muted small" }, [extra]) : "",
  ]);
  if (onClick) item.addEventListener("click", onClick);
  return item;
}

function renderStatusStrip(d) {
  const box = document.getElementById("status-strip");
  const items = [];
  const running = (d.jobs || []).filter(j => j.status === "running");

  // 1. синки — с прогрессом
  running.filter(j => j.progress).forEach(j => {
    const p = j.progress;
    let title = `${KIND_TITLE[j.kind] || "Синхронизация"} «${p.name}»`;
    if (p.count > 1) title = `${KIND_TITLE[j.kind]} · «${p.name}» (${p.index} из ${p.count})`;
    const dl = p.phase === "download";
    const right = dl && p.total
      ? [el("div", { class: "strip-count" }, [`${p.processed} / ${p.total}`]),
         p.eta_seconds != null ? el("div", { class: "muted small" }, [fmtLeft(p.eta_seconds)]) : ""]
      : null;
    const stats = [];
    if (p.downloaded) stats.push(`скачано ${p.downloaded}`);
    if (p.skipped) stats.push(`уже было ${p.skipped}`);
    stats.push(...failParts(p));
    if (p.yt_blocked) stats.push("⚠ " + YT_BLOCK_HINT);
    items.push(stripItem({
      icon: "⟳", iconCls: "spin", title,
      sub: dl ? (p.current ? `Сейчас: ${p.current}` : "качаю…") : syncDetail(p),
      right, bar: progressBar(dl ? p.processed : null, p.total),
      extra: stats.join(" · "), onClick: () => openJobLog(j.id),
    }));
  });

  // 2. загрузки треков (Ненайденные): одна строка на всё
  const tracks = running.filter(j => !j.progress && !["sync", "sync-all", "autosync"].includes(j.kind));
  const batch = (d.groups || []).find(g => g.running > 0 || g.queued > 0);
  if (tracks.length || batch) {
    const cur = tracks[0];
    const src = cur && SOURCE[cur.kind] ? SOURCE[cur.kind].label : "";
    const title = batch ? `Загрузка треков «${batch.name}»` : "Загрузка трека";
    const sub = cur ? `Сейчас: ${cur.subject || cur.name}` + (src ? ` · ${src}` : "") : "";
    const right = batch ? [el("div", { class: "strip-count" }, [`${batch.done + batch.failed} / ${batch.total}`])] : null;
    const extra = [];
    if (batch && batch.failed) extra.push(`не удалось ${batch.failed}`);
    if (d.queued) extra.push(`в очереди ${d.queued}`);
    items.push(stripItem({
      icon: "↓", iconCls: "pulse", title, sub, right,
      bar: batch ? progressBar(batch.done + batch.failed, batch.total) : null,
      extra: extra.join(" · "), onClick: () => cur ? openJobLog(cur.id) : switchView("jobs"),
    }));
  }

  // 3. апгрейд до 320k
  const u = d.upgrade;
  if (u && ["running", "waiting", "stopping"].includes(u.state)) {
    const processed = u.done + u.failed + u.skipped;
    const sub = u.state === "waiting" ? "пауза до завтра — дневной лимит"
      : u.state === "stopping" ? "останавливается…"
      : (u.current ? `Сейчас: ${u.current}` : "");
    items.push(stripItem({
      icon: "↑", iconCls: "pulse", title: "Апгрейд до 320k", sub,
      right: [el("div", { class: "strip-count" }, [`${processed} / ${u.total}`]),
              u.eta_seconds != null ? el("div", { class: "muted small" }, [fmtLeft(u.eta_seconds)]) : ""],
      bar: progressBar(processed, u.total),
      extra: [`улучшено ${u.done}`, u.skipped ? `уже хорошие ${u.skipped}` : "", u.failed ? `ошибок ${u.failed}` : ""]
        .filter(Boolean).join(" · "),
      onClick: () => switchView("upgrade"),
    }));
  }

  // 4. простой
  if (!items.length) {
    const ls = d.last_sync;
    const parts = [];
    if (ls) {
      let t = `последний синк «${ls.name}» ${fmtAgo(ls.ts)}`;
      if (ls.downloaded) t += `, +${ls.downloaded} ${plural(ls.downloaded, "трек", "трека", "треков")}`;
      parts.push(t);
    }
    if (d.next_autosync) {
      const s = d.next_autosync - Date.now() / 1000;
      parts.push(s > 60 ? `следующий автосинк ${fmtIn(s)}` : "автосинк вот-вот начнётся");
    }
    if (d.queued) parts.push(`в очереди ${d.queued}`);
    items.push(stripItem({
      icon: "✓", iconCls: "ok", title: d.queued ? "Ждёт очереди" : "Всё спокойно",
      sub: parts.join(" · ") || "синхронизаций ещё не было",
    }));
  }
  box.replaceChildren(...items);
}
function fmtIn(sec) {
  if (sec < 3600) return `через ${Math.round(sec / 60)} мин`;
  if (sec < 86400) return `через ${Math.round(sec / 3600)} ч`;
  return `через ${Math.round(sec / 86400)} дн`;
}

// ----- действия с задачами
function startTaskPolling() { pollTasks(); }
async function cancelJob(jobId) {
  try { await api("POST", "/api/jobs/" + jobId + "/cancel"); pollTasks(); }
  catch (e) { toast("Не отменить: " + e.message); }
}
async function removeJob(jobId) {
  try {
    await api("DELETE", "/api/jobs/" + jobId);
    if (SEL_JOB === jobId) hideLog();
    pollTasks();
  } catch (e) { toast("Не удалить: " + e.message); }
}
// отмена/удаление всей группы (пакетной загрузки плейлиста) одной кнопкой
async function cancelGroup(title) {
  try { await api("POST", "/api/jobs-group/cancel", { title }); pollTasks(); }
  catch (e) { toast("Не отменить: " + e.message); }
}
async function removeGroup(title) {
  try { await api("POST", "/api/jobs-group/remove", { title }); pollTasks(); }
  catch (e) { toast("Не удалить: " + e.message); }
}

// ----- опрос
async function pollTasks() {
  clearTimeout(TASK_POLL_TIMER);
  TASK_POLL_TIMER = null;
  // на экране входа не опрашиваем: каждый 401 снова дёргал бы экран входа
  if (document.getElementById("appmain").style.display === "none") return;
  // Вкладка не видна — не опрашиваем совсем. Сервис почти всё время простаивает,
  // а забытая открытая вкладка иначе будила бы бэкенд круглосуточно.
  if (document.hidden) { TASK_POLL_PAUSED = true; return; }
  TASK_POLL_PAUSED = false;
  let data = null;
  try { data = await api("GET", "/api/jobs?limit=100"); } catch (e) { /* молча, повторим */ }
  const jobs = (data && data.jobs) || [];
  const groups = (data && data.groups) || [];
  const activeTotal = (data && data.active) || 0;

  // строки «Ненайденных» — по статусу их задач скачивания/заливки
  jobs.forEach(j => {
    const row = PENDING.get(j.id);
    if (!row) return;
    if (j.status === "running") {
      setRowState(row, "running", { jobId: j.id });
    } else if (j.status === "done") {
      row.rowEl.classList.add("row-done");
      setTimeout(() => { if (row.rowEl.parentNode) row.rowEl.parentNode.removeChild(row.rowEl); }, 400);
      PENDING.delete(j.id);
    } else if (j.status === "error") {
      setRowState(row, "error");           // компактный «⚠ Ошибка · Повторить», детали — в «Активности»
      PENDING.delete(j.id);
    } else if (j.status === "cancelled") {
      setRowState(row, "idle");            // вернуть кнопки-источники
      PENDING.delete(j.id);
    } else if (j.status === "queued") {
      setRowState(row, "queued", { jobId: j.id, pos: j.queue_pos });
    }
  });

  if (data) {
    renderStatusStrip(data);
    // синк плейлиста закончился — обновить сводку карточек (треков, не найдено, когда)
    const nowSyncs = data.syncs || {};
    const ended = Object.keys(LIVE_SYNCS).some(id => !nowSyncs[id]);
    LIVE_SYNCS = nowSyncs;
    const plView = document.getElementById("view-playlists").classList.contains("active");
    if (plView) { if (ended) loadPlaylists(); else updatePlaylistLive(LIVE_SYNCS); }
  }

  // «Активность»: агрегаты пакетных загрузок + одиночные задачи вне групп
  const groupTitles = new Set(groups.map(g => g.title));
  const display = groups.concat(jobs.filter(j => !groupTitles.has(j.title)));
  const isActive = e => e.group ? (e.running > 0 || e.queued > 0) : (e.status === "running" || e.status === "queued");
  const isRunning = e => e.group ? e.running > 0 : e.status === "running";
  display.sort((a, b) => (isActive(b) - isActive(a)) || (isRunning(b) - isRunning(a)));
  renderTaskList(display);
  if (SEL_JOB) refreshSelectedLog();

  // Пока что-то идёт — раз в 1.5 с; апгрейд меняется медленно — раз в 5 с;
  // в простое — редко: ничего не меняется, а бэкенд на каждый запрос проходит по реестру.
  const upg = data && data.upgrade && ["running", "waiting", "stopping"].includes(data.upgrade.state);
  const delay = (activeTotal > 0 || PENDING.size > 0) ? 1500 : (upg ? 5000 : 15000);
  TASK_POLL_TIMER = setTimeout(pollTasks, delay);
}

// Вернулись на вкладку — сразу догоняем состояние (и возобновляем опрос).
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (TASK_POLL_PAUSED) pollTasks();
  // опрос апгрейда тоже вставал вместе со скрытой вкладкой — возобновляем
  const upg = document.getElementById("view-upgrade");
  if (upg && upg.classList.contains("active")) loadUpgrade();
});

// ----- вкладка «Активность»
// Инкрементальный рендер: НЕ пересоздаём DOM каждый опрос, иначе сбрасывается
// выделение текста в открытом логе. Обновляем строки на месте.
const TASK_NODES = new Map();  // id -> {row, ...}

function jobTitle(j) {
  if (j.group) return `Загрузка треков «${j.name}»`;
  if (KIND_TITLE[j.kind]) return j.kind === "sync" ? `Синхронизация «${j.name}»` : KIND_TITLE[j.kind];
  return j.subject || j.name;
}
function jobDetail(j) {
  if (j.group) {
    const parts = [`${j.done} из ${j.total}`];
    if (j.failed) parts.push(`не удалось ${j.failed}`);
    if (j.queued) parts.push(`в очереди ${j.queued}`);
    return parts.join(" · ");
  }
  const p = j.progress;
  if (p && j.status === "running") {
    const where = p.count > 1 ? `«${p.name}» (${p.index} из ${p.count}) · ` : "";
    return where + syncDetail(p);
  }
  const parts = [];
  if (p && j.status !== "queued") {
    const s = syncSummary(p);
    if (s) parts.push((p.count > 1 ? `последний «${p.name}»: ` : "") + s);
  } else if (!p && j.subject && j.name) {
    parts.push(`плейлист «${j.name}»`);
  }
  if (j.finished && j.status !== "running") parts.push(fmtAgo(j.finished));
  return parts.join(" · ");
}
function _groupStatus(j) {
  if (j.running || j.queued) return { text: "идёт", cls: "running" };
  if (j.failed && !j.done) return { text: "ошибка", cls: "error" };
  return { text: j.failed ? "готово, есть ошибки" : "готово", cls: j.failed ? "cancelled" : "done" };
}
function _logId(j) { return j.group ? j.current_id : j.id; }

function _makeTaskNode(j) {
  const tag = j.group ? _sourceTag("download") : _sourceTag(j.kind);
  const titleEl = el("div", { class: "act-title" }, [""]);
  const detailEl = el("div", { class: "act-detail muted small" }, [""]);
  const barWrap = el("div", { class: "act-bar" });
  const statusEl = el("span", { class: "status" }, [""]);
  const copyBtn = el("button", { class: "tact copy", title: "Копировать лог" }, ["📋"]);
  const actBtn = el("button", { class: "tact" }, [""]);
  const row = el("div", { class: "act-row" + (j.group ? " group" : "") }, [
    el("div", { class: "act-main" }, [
      el("div", { class: "act-line" }, [tag || "", titleEl]),
      detailEl, barWrap,
    ]),
    statusEl, copyBtn, actBtn,
  ]);
  const node = { row, titleEl, detailEl, barWrap, statusEl, copyBtn, actBtn, job: j };
  copyBtn.addEventListener("click", (ev) => { ev.stopPropagation(); const id = _logId(node.job); if (id) copyJobLog(id); });
  actBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const cur = node.job;
    const active = cur.group ? (cur.running > 0 || cur.queued > 0) : (cur.status === "queued" || cur.status === "running");
    if (cur.group) { if (active) cancelGroup(cur.title); else removeGroup(cur.title); }
    else { if (active) cancelJob(cur.id); else removeJob(cur.id); }
  });
  row.addEventListener("click", () => { const id = _logId(node.job); if (id) selectTask(id); });
  _updateTaskNode(node, j);
  return node;
}
function _updateTaskNode(node, j) {
  node.job = j;
  node.titleEl.textContent = jobTitle(j);
  node.detailEl.textContent = jobDetail(j);
  // полоса — у идущих синков и пакетных загрузок
  node.barWrap.innerHTML = "";
  if (j.group && (j.running || j.queued)) node.barWrap.appendChild(progressBar(j.done + j.failed, j.total));
  else if (!j.group && j.status === "running" && j.progress) {
    const p = j.progress;
    node.barWrap.appendChild(progressBar(p.phase === "download" ? p.processed : null, p.total));
  }
  const st = j.group ? _groupStatus(j) : { text: statusRu(j.status, j.queue_pos), cls: j.status };
  node.statusEl.className = "status " + st.cls;
  node.statusEl.textContent = st.text;
  node.copyBtn.style.display = _logId(j) ? "" : "none";
  const active = j.group ? (j.running > 0 || j.queued > 0) : (j.status === "queued" || j.status === "running");
  node.actBtn.className = "tact " + (active ? "cancel" : "remove");
  node.actBtn.textContent = active ? "✕" : "🗑";
  node.actBtn.title = active ? (j.group ? "Отменить всю группу" : "Отменить") : "Убрать из списка";
  node.row.classList.toggle("sel", !!_logId(j) && SEL_JOB === _logId(j));
}

function renderTaskList(jobs) {
  const list = document.getElementById("activity-list");
  if (!jobs.length) {
    for (const [, n] of TASK_NODES) n.row.remove();
    TASK_NODES.clear();
    hideLog();
    if (!list.querySelector(".empty-state")) {
      list.replaceChildren(el("div", { class: "empty-state muted" },
        ["Пока пусто. Здесь появятся синхронизации и загрузки треков."]));
    }
    return;
  }
  const empty = list.querySelector(".empty-state");
  if (empty) empty.remove();

  const recent = jobs.slice(0, 100);
  const want = new Set(recent.map(j => j.id));
  for (const [id, n] of [...TASK_NODES]) {
    if (!want.has(id)) {
      n.row.remove(); TASK_NODES.delete(id);
      if (SEL_JOB === id) hideLog();
    }
  }
  // существующие узлы НЕ двигаем (иначе сбрасывалось бы выделение/лог), только
  // обновляем. Новые вставляем сверху, перебирая в обратном порядке → newest-first.
  for (let i = recent.length - 1; i >= 0; i--) {
    const j = recent[i];
    let node = TASK_NODES.get(j.id);
    if (!node) {
      node = _makeTaskNode(j);
      TASK_NODES.set(j.id, node);
      list.insertBefore(node.row, list.firstChild);
    }
    _updateTaskNode(node, j);
  }
  // лог под выбранной строкой — двигаем ТОЛЬКО если он не на месте. У группы
  // строка имеет id "grp:…", поэтому ищем узел ещё и по _logId.
  let sel = SEL_JOB ? TASK_NODES.get(SEL_JOB) : null;
  if (!sel && SEL_JOB) {
    for (const [, n] of TASK_NODES) { if (_logId(n.job) === SEL_JOB) { sel = n; break; } }
  }
  if (sel) {
    if (sel.row.nextSibling !== LOG_EL) sel.row.insertAdjacentElement("afterend", LOG_EL);
    LOG_EL.style.display = "block";
  } else if (LOG_EL.parentNode && LOG_EL.parentNode.id === "activity-list") {
    LOG_EL.remove(); LOG_EL.style.display = "none";
  }
}

function selectTask(jobId) {
  if (SEL_JOB === jobId) { hideLog(); pollTasks(); return; }  // повторный клик — закрыть
  SEL_JOB = jobId; SEL_LOG_OFFSET = 0; LOG_EL.textContent = "";
  pollTasks();   // немедленно перерисует список и подтянет лог под строкой
}

async function refreshSelectedLog() {
  if (!SEL_JOB) return;
  try {
    const data = await api("GET", "/api/jobs/" + SEL_JOB + "?since=" + SEL_LOG_OFFSET);
    const job = data.job;
    if (job.log && job.log.length) {
      const atBottom = LOG_EL.scrollHeight - LOG_EL.scrollTop - LOG_EL.clientHeight < 40;
      // appendChild новой ноды НЕ трогает существующий текст → выделение не сбрасывается
      LOG_EL.appendChild(document.createTextNode(job.log.join("\n") + "\n"));
      SEL_LOG_OFFSET = job.log_offset + job.log.length;
      if (atBottom) LOG_EL.scrollTop = LOG_EL.scrollHeight;
    }
  } catch (e) { /* игнор */ }
}

// Копировать весь лог задачи (с фолбэком для http — без navigator.clipboard).
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text); return true;
    }
  } catch (e) { /* фолбэк ниже */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}
async function copyJobLog(jobId) {
  try {
    const data = await api("GET", "/api/jobs/" + jobId + "?since=0");
    const text = (data.job.log || []).join("\n");
    const ok = await copyText(text);
    toast(ok ? "Лог скопирован" : "Не удалось скопировать");
  } catch (e) { toast("Ошибка: " + e.message); }
}

// ------------------------------------------------------------------ вход
// Режимы сервера: вход отключён (AUTH_ENABLED=false) / пароль ещё не задан
// (первичная установка) / обычный вход. В браузере хранится токен сессии, не пароль.
let AUTH = { enabled: true, setup_required: false, source: "", min_length: 6 };

function showAuthScreen(mode) {   // "login" | "setup"
  const setup = mode === "setup";
  const scr = document.getElementById("login-screen");
  scr.dataset.mode = mode;
  document.getElementById("login-title").textContent = setup ? "Придумайте пароль" : "Вход";
  const note = document.getElementById("login-note");
  note.style.display = setup ? "" : "none";
  note.textContent = setup ? `Пароль для входа в webui, минимум ${AUTH.min_length} символов. ` +
    "На сервере хранится только его хэш; сменить можно во вкладке «Настройки»." : "";
  document.getElementById("login-confirm-wrap").style.display = setup ? "" : "none";
  document.getElementById("login-token").setAttribute("autocomplete", setup ? "new-password" : "current-password");
  document.getElementById("login-btn").textContent = setup ? "Сохранить и войти" : "Войти";
  document.getElementById("login-err").textContent = "";
  document.getElementById("appheader").style.display = "none";
  document.getElementById("appmain").style.display = "none";
  scr.style.display = "flex";
  document.getElementById("login-token").focus();
}

async function bootAuth() {
  let s;
  try {
    const res = await fetch("/api/auth/status", { headers: authHeaders() });
    s = await res.json();
  } catch (e) {
    showAuthScreen("login");
    document.getElementById("login-err").textContent = "Сервер недоступен — обновите страницу";
    return;
  }
  AUTH = s;
  document.getElementById("logout-btn").style.display = s.enabled ? "" : "none";
  if (!s.enabled || s.authenticated) {
    document.getElementById("login-screen").style.display = "none";
    showApp();
    return;
  }
  TOKEN = "";
  localStorage.removeItem(TOKEN_KEY);
  showAuthScreen(s.setup_required ? "setup" : "login");
}

async function submitAuth() {
  const mode = document.getElementById("login-screen").dataset.mode;
  const pwEl = document.getElementById("login-token");
  const cfEl = document.getElementById("login-confirm");
  const err = document.getElementById("login-err");
  const pw = pwEl.value;   // без trim: пробелы — законная часть пароля
  err.textContent = "";
  if (!pw) { err.textContent = "Введите пароль"; return; }
  if (mode === "setup") {
    if (pw.length < AUTH.min_length) { err.textContent = `Минимум ${AUTH.min_length} символов`; return; }
    if (pw !== cfEl.value) { err.textContent = "Пароли не совпадают"; return; }
  }
  let res, data;
  try {
    res = await fetch(mode === "setup" ? "/api/auth/setup" : "/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) { err.textContent = "Сервер недоступен"; return; }
  if (!res.ok) {
    err.textContent = data.detail || ("HTTP " + res.status);
    if (res.status === 409) bootAuth();   // режим сменился (пароль задали с другого устройства)
    return;
  }
  TOKEN = data.token || "";
  localStorage.setItem(TOKEN_KEY, TOKEN);
  pwEl.value = ""; cfEl.value = "";
  bootAuth();
}
document.getElementById("login-btn").addEventListener("click", submitAuth);
["login-token", "login-confirm"].forEach(id =>
  document.getElementById(id).addEventListener("keydown", e => { if (e.key === "Enter") submitAuth(); }));
document.getElementById("logout-btn").addEventListener("click", logout);

bootAuth();
