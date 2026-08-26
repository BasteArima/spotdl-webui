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
function logout() {
  TOKEN = "";
  localStorage.removeItem(TOKEN_KEY);
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("appheader").style.display = "none";
  document.getElementById("appmain").style.display = "none";
}
async function tryLogin(token) {
  TOKEN = token;
  const res = await fetch("/api/playlists", { headers: authHeaders() });
  if (res.ok) { localStorage.setItem(TOKEN_KEY, token); showApp(); return true; }
  TOKEN = "";
  return false;
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
    if (view === "jobs") loadJobs();
  });
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
        el("td", {}, [el("span", { class: "tag err" }, [t.error_type || "?"])]),
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
    grand ? `всего ненайдено: ${grand}` + (total !== grand ? ` (показано: ${total})` : "") : "";

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
    showTasks();
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
    if (!quiet) { toast(`Попытка ${label} в очереди`); showTasks(); pollTasks(); }
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
    showTasks(); pollTasks();
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
  showTasks(); pollTasks();
}

// ------------------------------------------------------------------ плейлисты
let EDIT_URL = null;
async function loadPlaylists() {
  try {
    const data = await api("GET", "/api/playlists");
    const tb = document.querySelector("#pl-table tbody");
    tb.innerHTML = "";
    (data.playlists || []).forEach(p => {
      const editBtn = el("button", { class: "btn secondary small" }, ["✎"]);
      editBtn.addEventListener("click", () => openPlModal(p));
      const delBtn = el("button", { class: "btn danger small" }, ["🗑"]);
      delBtn.addEventListener("click", () => deletePlaylist(p));
      const syncBtn = el("button", { class: "btn small" }, ["Sync"]);
      syncBtn.addEventListener("click", () => syncPlaylist(p, syncBtn));
      tb.appendChild(el("tr", {}, [
        el("td", {}, [p.name]),
        el("td", {}, [el("span", { class: "tag " + p.type }, [p.type])]),
        el("td", {}, [el("a", { href: p.url, target: "_blank", rel: "noopener", class: "small pl-url", title: p.url }, [p.url])]),
        el("td", {}, [el("div", { class: "pl-actions" }, [syncBtn, editBtn, delBtn])]),
      ]));
    });
  } catch (e) { toast("Ошибка: " + e.message); }
}

async function syncPlaylist(p, btn) {
  btn.disabled = true;
  try { await api("POST", "/api/sync", { url: p.url }); toast("Синхронизация в очереди"); showTasks(); pollTasks(); }
  catch (e) { toast("Ошибка: " + e.message); }
  finally { btn.disabled = false; }
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

// ------------------------------------------------------------------ вкладка «Задачи»
// вкладка «Задачи»: тянем БОЛЬШЕ задач (видеть всю очередь), скролл + клиентская сортировка.
let JOBS_CACHE = [];
let JOBS_SORT = { col: null, dir: 1 };           // col: title|status; dir: 1/-1
const STATUS_RANK = { running: 0, queued: 1, done: 2, cancelled: 3, error: 4 };
function _jobSortKey(j, col) {
  if (col === "status") return [STATUS_RANK[j.status] != null ? STATUS_RANK[j.status] : 9, j.queue_pos || 0];
  return [_taskName(j).toLowerCase()];
}
function renderJobsTable() {
  const tb = document.querySelector("#jobs-table tbody");
  tb.innerHTML = "";
  let rows = JOBS_CACHE.slice();
  if (JOBS_SORT.col) {
    rows.sort((a, b) => {
      const ka = _jobSortKey(a, JOBS_SORT.col), kb = _jobSortKey(b, JOBS_SORT.col);
      for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
        if (ka[i] < kb[i]) return -JOBS_SORT.dir;
        if (ka[i] > kb[i]) return JOBS_SORT.dir;
      }
      return 0;
    });
  }
  rows.forEach(j => {
    const viewBtn = el("button", { class: "btn secondary small" }, ["Лог"]);
    const logId = j.group ? j.current_id : j.id;
    if (logId) viewBtn.addEventListener("click", () => selectTask(logId, j.title));
    else viewBtn.disabled = true;
    let statusCell;
    if (j.group) {
      const b = _groupBadge(j);
      statusCell = el("span", { class: "status " + j.status, title: b.tip }, [b.txt]);
    } else {
      statusCell = el("span", { class: "status " + j.status }, [statusRu(j.status, j.queue_pos)]);
    }
    tb.appendChild(el("tr", { class: j.group ? "job-group" : "" }, [
      el("td", {}, _nameContent(j)),
      el("td", {}, [statusCell]),
      el("td", {}, [viewBtn]),
    ]));
  });
}
async function loadJobs() {
  try {
    // как в панели: агрегаты групп (пакетные загрузки) + одиночные задачи вне групп
    const data = await api("GET", "/api/jobs?limit=500");
    const groups = data.groups || [];
    const gTitles = new Set(groups.map(g => g.title));
    JOBS_CACHE = groups.concat((data.jobs || []).filter(j => !gTitles.has(j.title)));
    renderJobsTable();
  } catch (e) { toast("Ошибка: " + e.message); }
}
document.getElementById("jobs-reload").addEventListener("click", loadJobs);
// клик по заголовку колонки — сортировка (повторный клик меняет направление)
document.querySelectorAll("#jobs-table thead th[data-sort]").forEach(th => {
  if (!th.dataset.label) th.dataset.label = th.textContent;   // запоминаем базовую подпись
  th.addEventListener("click", () => {
    const col = th.dataset.sort;
    if (JOBS_SORT.col === col) JOBS_SORT.dir *= -1; else { JOBS_SORT.col = col; JOBS_SORT.dir = 1; }
    document.querySelectorAll("#jobs-table thead th[data-sort]").forEach(h => {
      h.textContent = h.dataset.label + (h.dataset.sort === JOBS_SORT.col ? (JOBS_SORT.dir > 0 ? " ↑" : " ↓") : "");
    });
    renderJobsTable();
  });
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
  try { await api("POST", "/api/sync-all"); toast("Синхронизация всех в очереди"); showTasks(); pollTasks(); }
  catch (e) { toast("Ошибка: " + e.message); }
});

// ------------------------------------------------------------------ панель задач (справа снизу)
let TASK_POLL_TIMER = null;
let TASK_POLL_PAUSED = false;   // опрос остановлен, т.к. вкладка не видна
let SEL_JOB = null;        // id задачи, чей лог открыт (инлайн под строкой)
let SEL_LOG_OFFSET = 0;
const LOG_EL = document.getElementById("tasklog");  // переносим под выбранную строку

function showTasks() { document.getElementById("taskspanel").classList.add("visible"); }
function hideLog() { SEL_JOB = null; if (LOG_EL.parentNode) LOG_EL.remove(); LOG_EL.style.display = "none"; }

function updateCollapseArrow() {
  const collapsed = document.getElementById("taskspanel").classList.contains("collapsed");
  // развёрнуто (очередь видна) → стрелка вниз; свёрнуто (скрыто) → вверх
  document.getElementById("tasks-collapse").textContent = collapsed ? "▴" : "▾";
}
// клик по всей строке-шапке (а не по пиксельной кнопке) сворачивает/разворачивает
document.getElementById("taskshead").addEventListener("click", () => {
  const collapsed = document.getElementById("taskspanel").classList.toggle("collapsed");
  if (collapsed) hideLog();           // сворачивание закрывает и открытый лог
  updateCollapseArrow();
});
updateCollapseArrow();

// ----- изменение размера панели «Задачи» (ручка в левом-верхнем углу) -----
// Панель закреплена за правый-нижний угол (right/bottom fixed), поэтому увеличение
// ширины/высоты раздвигает её ВЛЕВО и ВВЕРХ — прочь от контента. Размер сохраняется.
(function initTasksResize() {
  const SIZE_KEY = "spotdl_tasks_size";
  const MIN_W = 280, MIN_H = 160;
  const panel = document.getElementById("taskspanel");
  const handle = el("div", { class: "task-resize", title: "Потянуть — изменить размер" });
  panel.appendChild(handle);

  let lastSize = null;
  function applySize(w, h) {
    const maxW = window.innerWidth - 32, maxH = window.innerHeight - 32;
    w = Math.max(MIN_W, Math.min(w, maxW));
    h = Math.max(MIN_H, Math.min(h, maxH));
    panel.style.width = w + "px";
    // высоту НЕ фиксируем, а ограничиваем максимум: короткий список (1 задача)
    // ужимает панель до своего размера, длинный — упирается в этот максимум и скроллится.
    panel.style.height = "";
    panel.style.maxHeight = h + "px";
    panel.classList.add("resized");
    lastSize = { w, h };
    return lastSize;
  }
  // восстановить сохранённый размер
  try {
    const s = JSON.parse(localStorage.getItem(SIZE_KEY) || "null");
    if (s && s.w && s.h) applySize(s.w, s.h);
  } catch (e) { /* игнор */ }

  let dragging = false, sx = 0, sy = 0, sw = 0, sh = 0;
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault(); e.stopPropagation();
    dragging = true;
    sx = e.clientX; sy = e.clientY;
    const r = panel.getBoundingClientRect();
    sw = r.width;
    // отталкиваемся от текущего заданного максимума (а не от ужатого по контенту размера)
    sh = panel.style.maxHeight ? parseFloat(panel.style.maxHeight) : r.height;
    handle.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    // тянем верх-левый угол: влево → шире, вверх → выше (предел высоты)
    applySize(sw + (sx - e.clientX), sh + (sy - e.clientY));
  });
  function endDrag() {
    if (!dragging) return;
    dragging = false;
    document.body.style.userSelect = "";
    if (lastSize) { try { localStorage.setItem(SIZE_KEY, JSON.stringify(lastSize)); } catch (e) { /* игнор */ } }
  }
  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
  handle.addEventListener("click", (e) => e.stopPropagation());  // не сворачивать по клику на ручке
  // не вылезать за экран при уменьшении окна
  window.addEventListener("resize", () => {
    if (!panel.classList.contains("resized") || !lastSize) return;
    applySize(lastSize.w, lastSize.h);
  });
})();

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

async function pollTasks() {
  clearTimeout(TASK_POLL_TIMER);
  TASK_POLL_TIMER = null;
  // Вкладка не видна — не опрашиваем совсем. Сервис почти всё время простаивает,
  // а забытая открытая вкладка иначе будила бы бэкенд круглосуточно.
  if (document.hidden) { TASK_POLL_PAUSED = true; return; }
  TASK_POLL_PAUSED = false;
  let jobs = [];
  let groups = [];
  let activeTotal = 0;
  try {
    const data = await api("GET", "/api/jobs");
    jobs = data.jobs || [];
    groups = data.groups || [];
    activeTotal = data.active || 0;   // реальное число активных по всему реестру
  } catch (e) { /* молча, повторим */ }

  // обновляем строки ненайденных по статусу их задач скачивания/заливки
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
      setRowState(row, "error");           // компактный «⚠ Ошибка · Повторить», детали — в панели
      PENDING.delete(j.id);
    } else if (j.status === "cancelled") {
      setRowState(row, "idle");            // вернуть кнопки-источники
      PENDING.delete(j.id);
    } else if (j.status === "queued") {
      setRowState(row, "queued", { jobId: j.id, pos: j.queue_pos });
    }
  });

  // отображение: агрегаты групп (пакетные загрузки) + одиночные задачи, чьи заголовки
  // НЕ вошли в группу. Так панель показывает 1 строку на плейлист с прогрессом X/Y,
  // а не кучу одинаковых. Синхронизация строк «Ненайденных» выше — по индивидуальным.
  const groupTitles = new Set(groups.map(g => g.title));
  const singles = jobs.filter(j => !groupTitles.has(j.title));
  const display = groups.concat(singles);
  const isActive = e => e.group ? (e.running > 0 || e.queued > 0) : (e.status === "running" || e.status === "queued");
  const isRunning = e => e.group ? e.running > 0 : e.status === "running";
  display.sort((a, b) => (isActive(b) - isActive(a)) || (isRunning(b) - isRunning(a)));
  renderTaskList(display, activeTotal);
  if (SEL_JOB) refreshSelectedLog();

  // В простое опрашиваем редко: ничего не меняется, а бэкенд на каждый запрос
  // проходит по реестру задач. Пока что-то идёт — прежние 1.5 с.
  const delay = (activeTotal > 0 || PENDING.size > 0) ? 1500 : 15000;
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

// Инкрементальный рендер списка задач: НЕ пересоздаём DOM каждый поллинг, иначе
// сбрасывается выделение текста в открытом логе. Обновляем строки на месте.
const TASK_NODES = new Map();  // id -> {row, statusEl, actBtn, job}

function _statusText(j) {
  return statusRu(j.status, j.queue_pos);
}
// текст и подсказка для агрегата группы (прогресс X/Y)
function _groupBadge(j) {
  const txt = `${j.done}/${j.total}` + (j.failed ? ` ⚠${j.failed}` : "");
  let tip = `готово ${j.done} из ${j.total}`;
  if (j.running) tip += ` · идёт ${j.running}`;
  if (j.queued) tip += ` · в очереди ${j.queued}`;
  if (j.failed) tip += ` · ошибок ${j.failed}`;
  if (j.cancelled) tip += ` · отменено ${j.cancelled}`;
  return { txt, tip };
}
// id для лога/выделения: у группы — текущий бегущий участник, у задачи — она сама
function _logId(j) { return j.group ? j.current_id : j.id; }

function _makeTaskNode(j) {
  const statusEl = el("span", { class: "status " + j.status }, [""]);
  const copyBtn = el("button", { class: "tact copy", title: "Копировать лог" }, ["📋"]);
  const actBtn = el("button", { class: "tact" }, [""]);
  const row = el("div", { class: "taskrow" + (j.group ? " group" : "") }, [
    el("span", { class: "tname" }, _nameContent(j)),
    statusEl, copyBtn, actBtn,
  ]);
  const node = { row, statusEl, copyBtn, actBtn, job: j };
  copyBtn.addEventListener("click", (ev) => { ev.stopPropagation(); const id = _logId(node.job); if (id) copyJobLog(id); });
  actBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const cur = node.job;
    const active = cur.status === "queued" || cur.status === "running";
    if (cur.group) { if (active) cancelGroup(cur.title); else removeGroup(cur.title); }
    else { if (active) cancelJob(cur.id); else removeJob(cur.id); }
  });
  row.addEventListener("click", () => { const id = _logId(node.job); if (id) selectTask(id); });
  _updateTaskNode(node, j);
  return node;
}
function _updateTaskNode(node, j) {
  node.job = j;
  node.statusEl.className = "status " + j.status;
  if (j.group) {
    const b = _groupBadge(j);
    node.statusEl.textContent = b.txt;
    node.statusEl.title = b.tip;
    node.copyBtn.style.display = j.current_id ? "" : "none";
  } else {
    node.statusEl.textContent = _statusText(j);
    node.statusEl.title = "";
    node.copyBtn.style.display = "";
  }
  const active = j.status === "queued" || j.status === "running";
  node.actBtn.className = "tact " + (active ? "cancel" : "remove");
  node.actBtn.textContent = active ? "✕" : "🗑";
  node.actBtn.title = active ? (j.group ? "Отменить всю группу" : "Отменить") : "Убрать из списка";
  node.row.classList.toggle("sel", !!_logId(j) && SEL_JOB === _logId(j));
}

function renderTaskList(jobs, activeTotal) {
  const panel = document.getElementById("taskspanel");
  const list = document.getElementById("taskslist");

  if (!jobs.length) {
    panel.classList.remove("visible");
    document.getElementById("tasks-summary").textContent = "";
    for (const [, n] of TASK_NODES) n.row.remove();
    TASK_NODES.clear();
    return;
  }
  panel.classList.add("visible");

  const recent = jobs.slice(0, 50);   // панель скроллится; активные всегда сверху (сортировка с бэка)
  const want = new Set(recent.map(j => j.id));
  // удалить пропавшие
  for (const [id, n] of [...TASK_NODES]) {
    if (!want.has(id)) {
      n.row.remove(); TASK_NODES.delete(id);
      if (SEL_JOB === id) hideLog();
    }
  }
  // upsert: существующие узлы НЕ двигаем (иначе сбрасывалось бы выделение/лог),
  // только обновляем на месте. Новые узлы вставляем prepend'ом, перебирая recent
  // в ОБРАТНОМ порядке → итог сверху вниз = newest-first (и на старте, и при добавлении).
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

  // инлайн-лог под выбранной строкой — двигаем ТОЛЬКО если он не на месте
  // (иначе перенос узла сбрасывал бы выделение/прокрутку при каждом поллинге).
  // SEL_JOB — id РЕАЛЬНОЙ задачи; для группы её строка имеет id "grp:…", поэтому
  // ищем узел по _logId (у группы это текущий бегущий участник).
  let sel = SEL_JOB ? TASK_NODES.get(SEL_JOB) : null;
  if (!sel && SEL_JOB) {
    for (const [, n] of TASK_NODES) { if (_logId(n.job) === SEL_JOB) { sel = n; break; } }
  }
  if (sel && !panel.classList.contains("collapsed")) {
    if (sel.row.nextSibling !== LOG_EL) sel.row.insertAdjacentElement("afterend", LOG_EL);
    LOG_EL.style.display = "block";
  } else if (LOG_EL.parentNode) {
    LOG_EL.remove(); LOG_EL.style.display = "none";
  }

  const active = activeTotal || 0;
  document.getElementById("tasks-summary").textContent = active ? `активно: ${active}` : "";
}

function selectTask(jobId) {
  if (SEL_JOB === jobId) { hideLog(); pollTasks(); return; }  // повторный клик — закрыть
  SEL_JOB = jobId; SEL_LOG_OFFSET = 0; LOG_EL.textContent = "";
  document.getElementById("taskspanel").classList.remove("collapsed");
  updateCollapseArrow();
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
document.getElementById("login-btn").addEventListener("click", async () => {
  const token = document.getElementById("login-token").value.trim();
  const err = document.getElementById("login-err");
  err.textContent = "";
  if (!token) { err.textContent = "Введите токен"; return; }
  const ok = await tryLogin(token);
  if (!ok) err.textContent = "Неверный токен";
});
document.getElementById("login-token").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("login-btn").click();
});
document.getElementById("logout-btn").addEventListener("click", logout);

if (TOKEN) {
  tryLogin(TOKEN).then(ok => { if (!ok) logout(); });
} else {
  document.getElementById("login-screen").style.display = "flex";
}
