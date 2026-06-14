"use strict";
// ------------------------------------------------------------------ авторизация
const TOKEN_KEY = "spotdl_webui_token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";

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
  document.getElementById("appheader").style.display = "flex";
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
let ROWS = [];          // {spotify_url, safe, input, statusEl, rowEl}
// PENDING: job.id -> ROW, для удаления строки по завершении её задачи скачивания
let PENDING = new Map();

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
      const statusEl = el("span", { class: "rowstatus" }, [""]);
      const dlBtn = el("button", { class: "btn small" }, ["Скачать"]);
      const dzBtn = el("button", { class: "btn secondary small", title: "Скачать с Deezer по ISRC (нужен ARL) — для треков, которых нет на YouTube" }, ["Deezer"]);
      const ztBtn = el("button", { class: "btn secondary small", title: "Скачать напрямую со Spotify (librespot, 320k с Premium). Нужны креды Spotify." }, ["Spotify 320k"]);
      const fileInput = el("input", { type: "file", accept: "audio/*,.mp3,.flac,.m4a,.opus,.ogg,.wav", style: "display:none" });
      const fileBtn = el("button", { class: "btn secondary small", title: "Залить локальный файл — получит мету и обложку со Spotify" }, ["📁 Файл"]);
      const q = encodeURIComponent(t.song || "");
      const rowEl = el("tr", {}, [
        el("td", {}, [t.song || el("span", { class: "muted" }, ["(имя не распознано)"])]),
        el("td", {}, [el("span", { class: "tag err" }, [t.error_type || "?"])]),
        el("td", {}, [el("a", { href: t.spotify_url, target: "_blank", rel: "noopener" }, ["Spotify ↗"])]),
        el("td", {}, [el("a", { href: "https://music.youtube.com/search?q=" + q, target: "_blank", rel: "noopener" }, ["искать на YT ↗"])]),
        el("td", {}, [input]),
        el("td", {}, [el("div", { class: "row" }, [dlBtn, dzBtn, ztBtn, fileBtn, fileInput, statusEl])]),
      ]);
      const row = { spotify_url: t.spotify_url, safe: g.safe, input, statusEl, rowEl };
      ROWS.push(row); groupRows.push(row);
      dlBtn.addEventListener("click", () => enqueueDownloads([row]));
      dzBtn.addEventListener("click", () => tryDeezer(row));
      ztBtn.addEventListener("click", () => trySource(row, "/api/zotify", "Spotify"));
      fileBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files[0]) uploadFile(row, fileInput.files[0]);
        fileInput.value = "";
      });
      tbody.appendChild(rowEl);
    });

    const dlGroupBtn = el("button", { class: "btn small", title: "Скачать все треки этого плейлиста выбранным сверху источником" }, ["Скачать все"]);
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
}

// Поставить в очередь скачивание для набора строк (1 или много) — НЕ блокирует.
async function enqueueDownloads(rows) {
  rows = (rows || []).filter(r => r.input.value.trim());
  if (!rows.length) { toast("Заполните поле источника (YouTube/YT-Music URL)"); return; }
  // валидация ссылок-источников: невалидные подсвечиваем и не отправляем
  const bad = rows.filter(r => validateSource(r.input.value));
  if (bad.length) {
    bad.forEach(r => {
      r.statusEl.className = "rowstatus error";
      r.statusEl.textContent = " " + validateSource(r.input.value);
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
        PENDING.set(d.id, row);
        row.input.disabled = true;
        row.statusEl.className = "rowstatus queued";
        row.statusEl.textContent = " в очереди";
      }
    });
    toast(`В очередь: ${(data.downloads || []).length} трек(ов)` +
          ((data.syncs || []).length ? `, обновление m3u: ${data.syncs.length}` : ""));
    showTasks();
    pollTasks();
  } catch (e) { toast("Ошибка: " + e.message); }
}

// Попытка скачать трек из альтернативного источника (Deezer/Spotify).
async function trySource(row, endpoint, label, extra) {
  row.statusEl.className = "rowstatus queued";
  row.statusEl.textContent = ` ${label} в очереди`;
  try {
    const body = Object.assign({ spotify_url: row.spotify_url, safe: row.safe }, extra || {});
    const data = await api("POST", endpoint, body);
    PENDING.set(data.job.id, row);
    toast(`Попытка ${label} в очереди`);
    showTasks(); pollTasks();
  } catch (e) {
    row.statusEl.className = "rowstatus error";
    row.statusEl.textContent = " " + e.message;
    toast(`${label}: ${e.message}`);
  }
}
function tryDeezer(row) { return trySource(row, "/api/deezer", "Deezer"); }

// Залить локальный файл: получит мету/обложку со Spotify и ляжет в библиотеку.
async function uploadFile(row, file) {
  const fd = new FormData();
  fd.append("spotify_url", row.spotify_url);
  fd.append("safe", row.safe);
  fd.append("file", file);
  row.statusEl.className = "rowstatus queued";
  row.statusEl.textContent = " загрузка файла…";
  try {
    // НЕ задаём Content-Type — браузер сам выставит multipart boundary.
    const res = await fetch("/api/upload", { method: "POST", headers: authHeaders(), body: fd });
    if (res.status === 401) { logout(); return; }
    let data = null; try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error((data && data.detail) || ("HTTP " + res.status));
    PENDING.set(data.job.id, row);
    row.input.disabled = true;
    row.statusEl.className = "rowstatus queued";
    row.statusEl.textContent = " файл в очереди";
    toast("Файл «" + file.name + "» в очереди");
    showTasks(); pollTasks();
  } catch (e) {
    row.statusEl.className = "rowstatus error";
    row.statusEl.textContent = " " + e.message;
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
  rows.forEach(r => trySource(r, endpoint, label, extra));
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
async function loadJobs() {
  try {
    const data = await api("GET", "/api/jobs");
    const tb = document.querySelector("#jobs-table tbody");
    tb.innerHTML = "";
    (data.jobs || []).forEach(j => {
      const viewBtn = el("button", { class: "btn secondary small" }, ["Лог"]);
      viewBtn.addEventListener("click", () => selectTask(j.id, j.title));
      tb.appendChild(el("tr", {}, [
        el("td", {}, [j.title]),
        el("td", {}, [j.kind]),
        el("td", {}, [el("span", { class: "status " + j.status }, [j.status])]),
        el("td", {}, [viewBtn]),
      ]));
    });
  } catch (e) { toast("Ошибка: " + e.message); }
}
document.getElementById("jobs-reload").addEventListener("click", loadJobs);

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
  if (["running", "stopping", "waiting"].includes(s.state) &&
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
document.getElementById("tasks-collapse").addEventListener("click", () => {
  const collapsed = document.getElementById("taskspanel").classList.toggle("collapsed");
  if (collapsed) hideLog();           // сворачивание закрывает и открытый лог
  updateCollapseArrow();
});
updateCollapseArrow();

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

async function pollTasks() {
  clearTimeout(TASK_POLL_TIMER);
  let jobs = [];
  try {
    const data = await api("GET", "/api/jobs");
    jobs = data.jobs || [];
  } catch (e) { /* молча, повторим */ }

  // обновляем строки ненайденных по статусу их задач скачивания/заливки
  let active = 0;
  jobs.forEach(j => {
    if (j.status === "queued" || j.status === "running") active++;
    const row = PENDING.get(j.id);
    if (!row) return;
    if (j.status === "running") {
      row.statusEl.className = "rowstatus running"; row.statusEl.textContent = " идёт…";
    } else if (j.status === "done") {
      row.rowEl.classList.add("row-done");
      setTimeout(() => { if (row.rowEl.parentNode) row.rowEl.parentNode.removeChild(row.rowEl); }, 400);
      PENDING.delete(j.id);
    } else if (j.status === "error") {
      row.rowEl.classList.add("row-error");
      row.input.disabled = false;
      row.statusEl.className = "rowstatus error"; row.statusEl.textContent = " ошибка — см. лог";
      PENDING.delete(j.id);
    } else if (j.status === "cancelled") {
      row.input.disabled = false;
      row.statusEl.className = "rowstatus cancelled"; row.statusEl.textContent = " отменено";
      PENDING.delete(j.id);
    } else if (j.status === "queued") {
      row.statusEl.className = "rowstatus queued";
      row.statusEl.textContent = j.queue_pos ? ` в очереди (#${j.queue_pos})` : " в очереди";
    }
  });

  renderTaskList(jobs);
  if (SEL_JOB) refreshSelectedLog();

  const delay = (active > 0 || PENDING.size > 0) ? 1500 : 4000;
  TASK_POLL_TIMER = setTimeout(pollTasks, delay);
}

// Инкрементальный рендер списка задач: НЕ пересоздаём DOM каждый поллинг, иначе
// сбрасывается выделение текста в открытом логе. Обновляем строки на месте.
const TASK_NODES = new Map();  // id -> {row, statusEl, actBtn, job}

function _statusText(j) {
  return (j.status === "queued" && j.queue_pos) ? `в очереди #${j.queue_pos}` : j.status;
}
function _makeTaskNode(j) {
  const statusEl = el("span", { class: "status " + j.status }, [_statusText(j)]);
  const copyBtn = el("button", { class: "tact copy", title: "Копировать весь лог" }, ["📋"]);
  const actBtn = el("button", { class: "tact" }, [""]);
  const row = el("div", { class: "taskrow" }, [
    el("span", { class: "tname" }, [j.title]),
    statusEl, copyBtn, actBtn,
  ]);
  const node = { row, statusEl, actBtn, job: j };
  copyBtn.addEventListener("click", (ev) => { ev.stopPropagation(); copyJobLog(node.job.id); });
  actBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const st = node.job.status;
    if (st === "queued" || st === "running") cancelJob(node.job.id); else removeJob(node.job.id);
  });
  row.addEventListener("click", () => selectTask(j.id));
  return node;
}
function _updateTaskNode(node, j) {
  node.job = j;
  node.statusEl.className = "status " + j.status;
  node.statusEl.textContent = _statusText(j);
  const active = j.status === "queued" || j.status === "running";
  node.actBtn.className = "tact " + (active ? "cancel" : "remove");
  node.actBtn.textContent = active ? "✕" : "🗑";
  node.actBtn.title = active ? "Отменить" : "Убрать из списка";
  node.row.classList.toggle("sel", SEL_JOB === j.id);
}

function renderTaskList(jobs) {
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

  const recent = jobs.slice(0, 12);
  const want = new Set(recent.map(j => j.id));
  // удалить пропавшие
  for (const [id, n] of [...TASK_NODES]) {
    if (!want.has(id)) {
      n.row.remove(); TASK_NODES.delete(id);
      if (SEL_JOB === id) hideLog();
    }
  }
  // upsert (newest-first: новые добавляем в начало, существующие не двигаем)
  recent.forEach(j => {
    let node = TASK_NODES.get(j.id);
    if (!node) {
      node = _makeTaskNode(j);
      TASK_NODES.set(j.id, node);
      list.insertBefore(node.row, list.firstChild);
    }
    _updateTaskNode(node, j);
  });

  // инлайн-лог под выбранной строкой — двигаем ТОЛЬКО если он не на месте
  // (иначе перенос узла сбрасывал бы выделение/прокрутку при каждом поллинге)
  const sel = SEL_JOB ? TASK_NODES.get(SEL_JOB) : null;
  if (sel && !panel.classList.contains("collapsed")) {
    if (sel.row.nextSibling !== LOG_EL) sel.row.insertAdjacentElement("afterend", LOG_EL);
    LOG_EL.style.display = "block";
  } else if (LOG_EL.parentNode) {
    LOG_EL.remove(); LOG_EL.style.display = "none";
  }

  const active = jobs.filter(j => j.status === "queued" || j.status === "running").length;
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
