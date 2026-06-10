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
  startTaskPolling();
}

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
      const fileInput = el("input", { type: "file", accept: "audio/*,.mp3,.flac,.m4a,.opus,.ogg,.wav", style: "display:none" });
      const fileBtn = el("button", { class: "btn secondary small", title: "Залить локальный файл — получит мету и обложку со Spotify" }, ["📁 Файл"]);
      const q = encodeURIComponent(t.song || "");
      const rowEl = el("tr", {}, [
        el("td", {}, [t.song || el("span", { class: "muted" }, ["(имя не распознано)"])]),
        el("td", {}, [el("span", { class: "tag err" }, [t.error_type || "?"])]),
        el("td", {}, [el("a", { href: t.spotify_url, target: "_blank", rel: "noopener" }, ["Spotify ↗"])]),
        el("td", {}, [el("a", { href: "https://music.youtube.com/search?q=" + q, target: "_blank", rel: "noopener" }, ["искать на YT ↗"])]),
        el("td", {}, [input]),
        el("td", {}, [el("div", { class: "row" }, [dlBtn, fileBtn, fileInput, statusEl])]),
      ]);
      const row = { spotify_url: t.spotify_url, safe: g.safe, input, statusEl, rowEl };
      ROWS.push(row); groupRows.push(row);
      dlBtn.addEventListener("click", () => enqueueDownloads([row]));
      fileBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files[0]) uploadFile(row, fileInput.files[0]);
        fileInput.value = "";
      });
      tbody.appendChild(rowEl);
    });

    const dlGroupBtn = el("button", { class: "btn small" }, ["Скачать все с источником"]);
    dlGroupBtn.addEventListener("click", () => enqueueDownloads(groupRows.filter(r => r.input.value.trim())));
    const retryBtn = el("button", { class: "btn secondary small" }, ["Авто-повтор (YTM/YT/SoundCloud)"]);
    retryBtn.addEventListener("click", () => doRetry(g.safe, retryBtn));

    cont.appendChild(el("div", { class: "card" }, [
      el("div", { class: "row", style: "justify-content:space-between;margin-bottom:10px" }, [
        el("div", {}, [
          el("strong", {}, [g.safe]),
          el("span", { class: "muted small" }, ["  " + tracks.length + " трек(ов)" + (g.timestamp ? " • " + g.timestamp : "")]),
        ]),
        el("div", { class: "row" }, [dlGroupBtn, retryBtn]),
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

async function doRetry(safe, btn) {
  btn.disabled = true;
  try {
    await api("POST", "/api/retry", { safe });
    toast("Авто-повтор поставлен в очередь");
    showTasks(); pollTasks();
  } catch (e) { toast("Ошибка: " + e.message); }
  finally { btn.disabled = false; }
}

document.getElementById("err-filter").addEventListener("input", renderErrors);
document.getElementById("err-playlist-filter").addEventListener("change", renderErrors);
document.getElementById("err-reload").addEventListener("click", () => { loadErrors(); loadStatus(); });
document.getElementById("err-download-all").addEventListener("click",
  () => enqueueDownloads(ROWS.filter(r => r.input.value.trim())));

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
        el("td", {}, [el("a", { href: p.url, target: "_blank", rel: "noopener", class: "small" }, [p.url])]),
        el("td", {}, [el("div", { class: "row" }, [syncBtn, editBtn, delBtn])]),
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

document.getElementById("syncall-btn").addEventListener("click", async () => {
  if (!confirm("Запустить синхронизацию всех плейлистов? Это может занять много времени.")) return;
  try { await api("POST", "/api/sync-all"); toast("Синхронизация всех в очереди"); showTasks(); pollTasks(); }
  catch (e) { toast("Ошибка: " + e.message); }
});

// ------------------------------------------------------------------ панель задач (справа снизу)
let TASK_POLL_TIMER = null;
let SEL_JOB = null;        // id задачи, чей лог открыт
let SEL_LOG_OFFSET = 0;

function showTasks() { document.getElementById("taskspanel").classList.add("visible"); }
document.getElementById("tasks-collapse").addEventListener("click", () => {
  document.getElementById("taskspanel").classList.toggle("collapsed");
});

function startTaskPolling() { pollTasks(); }

async function cancelJob(jobId) {
  try { await api("POST", "/api/jobs/" + jobId + "/cancel"); toast("Отмена запрошена"); pollTasks(); }
  catch (e) { toast("Не отменить: " + e.message); }
}

async function pollTasks() {
  clearTimeout(TASK_POLL_TIMER);
  let jobs = [];
  try {
    const data = await api("GET", "/api/jobs");
    jobs = data.jobs || [];
  } catch (e) { /* молча, повторим */ }

  // обновляем строки ненайденных по статусу их задач скачивания
  let active = 0;
  jobs.forEach(j => {
    if (j.status === "queued" || j.status === "running") active++;
    const row = PENDING.get(j.id);
    if (!row) return;
    if (j.status === "running") {
      row.statusEl.className = "rowstatus running"; row.statusEl.textContent = " качается…";
    } else if (j.status === "done") {
      // успех — строка больше не нужна
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
  if (jobs.length) showTasks();

  // если открыт лог выбранной задачи — подтягиваем
  if (SEL_JOB) refreshSelectedLog();

  // частим, пока что-то активно; иначе реже
  const delay = (active > 0 || PENDING.size > 0) ? 1500 : 4000;
  TASK_POLL_TIMER = setTimeout(pollTasks, delay);
}

function renderTaskList(jobs) {
  const list = document.getElementById("taskslist");
  list.innerHTML = "";
  const recent = jobs.slice(0, 12);
  recent.forEach(j => {
    const rowCls = "taskrow" + (SEL_JOB === j.id ? " sel" : "");
    const statusText = (j.status === "queued" && j.queue_pos) ? `queued #${j.queue_pos}` : j.status;
    const children = [
      el("span", { class: "tname" }, [j.title]),
      el("span", { class: "status " + j.status }, [statusText]),
    ];
    if (j.status === "queued" || j.status === "running") {
      const cancel = el("button", { class: "cancel", title: "Отменить" }, ["✕"]);
      cancel.addEventListener("click", (ev) => { ev.stopPropagation(); cancelJob(j.id); });
      children.push(cancel);
    }
    const tr = el("div", { class: rowCls }, children);
    tr.addEventListener("click", () => selectTask(j.id, j.title));
    list.appendChild(tr);
  });
  const active = jobs.filter(j => j.status === "queued" || j.status === "running").length;
  document.getElementById("tasks-summary").textContent =
    active ? `активно: ${active}` : (jobs.length ? "очередь пуста" : "");
}

function selectTask(jobId, title) {
  if (SEL_JOB === jobId) {  // повторный клик — закрыть лог
    SEL_JOB = null;
    document.getElementById("tasklog").style.display = "none";
    return;
  }
  SEL_JOB = jobId; SEL_LOG_OFFSET = 0;
  const pre = document.getElementById("tasklog");
  pre.style.display = "block"; pre.textContent = "";
  document.getElementById("taskspanel").classList.remove("collapsed");
  refreshSelectedLog();
}

async function refreshSelectedLog() {
  if (!SEL_JOB) return;
  try {
    const data = await api("GET", "/api/jobs/" + SEL_JOB + "?since=" + SEL_LOG_OFFSET);
    const job = data.job;
    const pre = document.getElementById("tasklog");
    if (job.log && job.log.length) {
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
      pre.textContent += job.log.join("\n") + "\n";
      SEL_LOG_OFFSET = job.log_offset + job.log.length;
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
  } catch (e) { /* игнор */ }
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
