// Каркас нового интерфейса: вход, меню/вкладки, переходы, тема, строка
// «что сейчас происходит». Разделы — модули в ./views, грузятся по требованию.
//
// Раздел экспортирует:
//   render(ctx)          → строка разметки (можно async)
//   mount?(root, ctx)    → навесить события; вернуть функцию очистки
//   onState?(root, st)   → обновить живые части при новом снимке очереди

import { authStatus, signIn, signOut } from "./api.js";
import { describeNow, startPolling, state, stopPolling, subscribe } from "./store.js";
import { bar, esc, fmtIn, icon, on, toast } from "./ui.js";

const NAV = [
  { id: "home", title: "Главная", icon: "home" },
  { id: "search", title: "Поиск", icon: "search" },
  { id: "playlists", title: "Плейлисты", icon: "playlist" },
  { id: "library", title: "Библиотека", icon: "vinyl" },
  { id: "quality", title: "Качество 320k", icon: "arrow-big-up-lines" },
  { id: "attention", title: "Внимание", icon: "alert-circle", badge: true },
  { id: "discover", title: "Открытия", icon: "sparkles" },
  { id: "activity", title: "Активность", icon: "history" },
  { id: "settings", title: "Настройки", icon: "settings" },
];
// на телефоне — пять вкладок, остальное в «Ещё»
const TABS = [
  { id: "home", title: "Главная", icon: "home" },
  { id: "search", title: "Поиск", icon: "search" },
  { id: "playlists", title: "Плейлисты", icon: "playlist" },
  { id: "attention", title: "Внимание", icon: "alert-circle", badge: true },
  { id: "more", title: "Ещё", icon: "dots" },
];
const MORE = ["library", "quality", "discover", "activity", "settings", "more"];

const VIEWS = {
  home: () => import("./views/home.js"),
  search: () => import("./views/stub.js"),
  playlists: () => import("./views/playlists.js"),
  library: () => import("./views/stub.js"),
  quality: () => import("./views/stub.js"),
  attention: () => import("./views/stub.js"),
  discover: () => import("./views/stub.js"),
  activity: () => import("./views/stub.js"),
  settings: () => import("./views/settings.js"),
  more: () => import("./views/more.js"),
};

const $ = (s) => document.querySelector(s);
let current = { view: null, param: null, cleanup: null, module: null };

// ---------------------------------------------------------------- тема
const THEME_KEY = "spotdl_theme";      // общий с прежним интерфейсом
export function getTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "auto";
  } catch (e) { return "auto"; }
}
export function setTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* ок */ }
}
setTheme(getTheme());

// ---------------------------------------------------------------- переходы
function parseHash() {
  const parts = (location.hash.replace(/^#\/?/, "") || "home").split("/");
  const view = VIEWS[parts[0]] ? parts[0] : "home";
  return { view, param: parts.slice(1).join("/") || null };
}
export function go(path) {
  const target = "#/" + path.replace(/^#?\/?/, "");
  if (location.hash === target) route();
  else location.hash = target;
}

function attentionCount() {
  const s = state.library && state.library.stats;
  return s ? (s.missing || 0) + (s.dupes_open || 0) : 0;
}

function renderNav() {
  const { view } = parseHash();
  const n = attentionCount();
  const isOn = (id) => view === id || (id === "more" && MORE.includes(view) && !NAV.slice(0, 4).some((x) => x.id === view));
  $("#nav").innerHTML = NAV.map((x) => `
    <a class="nav-item ${view === x.id ? "on" : ""}" href="#/${x.id}">${icon(x.icon)}${esc(x.title)}${
      x.badge && n ? `<span class="count">${n}</span>` : ""}</a>`).join("");
  $("#tabbar").innerHTML = TABS.map((x) => `
    <a class="${isOn(x.id) || view === x.id ? "on" : ""}" href="#/${x.id}">${icon(x.icon)}${esc(x.title)}${
      x.badge && n ? `<span class="dot">${n > 99 ? "99+" : n}</span>` : ""}</a>`).join("");
  const item = NAV.find((x) => x.id === view) || TABS.find((x) => x.id === view);
  $("#top-title").textContent = item ? item.title : "";
}

function renderSideStatus() {
  const now = describeNow(state.jobs);
  const el = $("#side-status");
  if (now) {
    el.innerHTML = `<a href="#/activity" class="side-now">
      <div class="row" style="gap:8px;margin-bottom:6px"><span class="${now.spin ? "spin" : ""}" style="color:var(--accent)">${icon(now.icon, "sm")}</span>
      <b class="ellip" style="color:var(--text);font-weight:600">${esc(now.title)}</b></div>
      ${now.pct != null ? bar(now.pct) : ""}<div style="margin-top:6px" class="ellip">${esc(now.sub || "")}</div></a>`;
  } else {
    const next = state.jobs && state.jobs.next_autosync;
    el.innerHTML = `<div class="row" style="gap:8px">${icon("circle-check", "sm")}<b style="color:var(--text);font-weight:600">Всё спокойно</b></div>
      <div style="margin-top:4px">${next ? "автосинк " + fmtIn(next - Date.now() / 1000) : "автосинк выключен"}</div>`;
  }
}

async function route() {
  if ($("#app").classList.contains("hidden")) return;   // экран входа — разделы не рисуем
  const { view, param } = parseHash();
  renderNav();
  if (current.cleanup) { try { current.cleanup(); } catch (e) { /* ок */ } }
  current = { view, param, cleanup: null, module: null };
  const main = $("#main");
  const ctx = { param, go, state, toast };
  try {
    const mod = await VIEWS[view]();
    if (parseHash().view !== view) return;          // пока грузили — ушли в другой раздел
    current.module = mod;
    main.innerHTML = await mod.render(Object.assign({ view, nav: NAV.find((x) => x.id === view) }, ctx));
    if (mod.mount) current.cleanup = mod.mount(main, ctx) || null;
    if (mod.onState) mod.onState(main, state);
  } catch (e) {
    console.error(e);
    main.innerHTML = `<div class="stub">${icon("alert-triangle")}<p>Не удалось открыть раздел: ${esc(e.message)}</p></div>`;
  }
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- вход
function showAuth(st) {
  stopPolling();
  $("#app").classList.add("hidden");
  const box = $("#auth");
  box.classList.remove("hidden");
  const setup = st.setup_required;
  box.innerHTML = `<form class="card" id="auth-form">
      <div class="brand" style="padding:0"><div class="logo">${icon("music")}</div>Музыка</div>
      <h1>${setup ? "Придумайте пароль" : "Вход"}</h1>
      <p class="muted small" style="margin:0">${setup
        ? `Пароль защитит сервис. Минимум ${st.min_length} символов.`
        : "Введите пароль сервиса."}</p>
      <input class="field" type="password" id="auth-pw" autocomplete="${setup ? "new-password" : "current-password"}" placeholder="Пароль" autofocus>
      ${setup ? `<input class="field" type="password" id="auth-pw2" autocomplete="new-password" placeholder="Ещё раз">` : ""}
      <div class="err" id="auth-err"></div>
      <button class="btn primary" type="submit">${setup ? "Сохранить и войти" : "Войти"}</button>
    </form>`;
  $("#auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = $("#auth-pw").value;              // без trim: пробелы — часть пароля
    const err = $("#auth-err");
    err.textContent = "";
    if (!pw) { err.textContent = "Введите пароль"; return; }
    if (setup) {
      if (pw.length < st.min_length) { err.textContent = `Минимум ${st.min_length} символов`; return; }
      if (pw !== $("#auth-pw2").value) { err.textContent = "Пароли не совпадают"; return; }
    }
    try {
      await signIn(setup ? "setup" : "login", pw);
      boot();
    } catch (ex) {
      err.textContent = ex.message;
      if (ex.status === 409) boot();             // режим сменился (пароль задали с другого устройства)
    }
  });
}

function showApp(st) {
  $("#auth").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout").classList.toggle("hidden", !st.enabled);
  startPolling();
  route();
}

async function boot() {
  let st;
  try {
    st = await authStatus();
  } catch (e) {
    $("#auth").classList.remove("hidden");
    $("#auth").innerHTML = `<div class="card"><h1>Сервер недоступен</h1><p class="muted">Обновите страницу через минуту.</p></div>`;
    return;
  }
  if (!st.enabled || st.authenticated) showApp(st);
  else showAuth(st);
}

// ---------------------------------------------------------------- старт
window.addEventListener("hashchange", route);
window.addEventListener("auth:lost", () => boot());
subscribe((st) => {
  renderNav();
  renderSideStatus();
  if (current.module && current.module.onState) {
    try { current.module.onState($("#main"), st); } catch (e) { console.error(e); }
  }
});
on(document.body, "click", "#logout", () => { signOut(); toast("Вы вышли"); });
boot();
