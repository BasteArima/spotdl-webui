// «Активность»: что сервис делал и делает — понятными словами; лог по нажатию
// (у идущей задачи дописывается на лету), отмена и уборка. Пакетные загрузки
// одного плейлиста — одной строкой.
import { api } from "../api.js";
import { pokeSoon } from "../store.js";
import { esc, fmtAgo, icon, on, toast } from "../ui.js";

let jobs = [];
let groups = [];
let filter = "all";
let open = null;            // id раскрытой задачи
let logState = { id: null, lines: [], offset: 0 };
let logTimer = null;
let limit = 60;

const KIND = {
  sync: "Синк", "sync-all": "Синк всех плейлистов", autosync: "Автосинк", index: "Обновление индекса библиотеки",
  download: "YouTube", deezer: "Deezer", zotify: "Spotify 320k", upload: "Свой файл", fetch: "Авто", merge: "Склейка дублей",
};

function title(j) {
  if (j.group) return `Загрузка треков «${j.name}»`;
  if (j.kind === "sync") return `Синк «${j.name}»`;
  if (j.kind === "sync-all" || j.kind === "autosync" || j.kind === "index" || j.kind === "merge") return KIND[j.kind];
  if (KIND[j.kind]) return `${KIND[j.kind]} · ${j.subject || j.name}`;
  return j.subject || j.title || j.name;
}

function detail(j) {
  if (j.group) {
    const parts = [`${j.done} из ${j.total}`];
    if (j.failed) parts.push(`не вышло ${j.failed}`);
    if (j.queued) parts.push(`в очереди ${j.queued}`);
    return parts.join(" · ");
  }
  const p = j.progress;
  if (p) {
    const parts = [];
    if (p.phase === "download" && p.total) parts.push(`${p.processed} из ${p.total}`);
    else if (j.status === "running") parts.push(p.phase === "list" ? "получаю список" : "собираю данные треков");
    if (p.downloaded) parts.push(`скачано ${p.downloaded}`);
    if (p.skipped) parts.push(`уже было ${p.skipped}`);
    if (p.lookup_failed) parts.push(`не найдено ${p.lookup_failed}`);
    if (p.download_failed) parts.push(`YouTube не отдал ${p.download_failed}`);
    if (j.status === "running" && p.current) parts.push(`сейчас: ${p.current}`);
    if (parts.length) return parts.join(" · ");
  }
  return { queued: "в очереди", running: "идёт", done: "готово", error: "не вышло — подробности в логе", cancelled: "отменено" }[j.status] || j.status;
}

function when(j) {
  if (j.status === "queued") return "ждёт";
  if (j.status === "running") return j.started ? `с ${new Date(j.started * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}` : "";
  return fmtAgo(j.finished || j.created);
}

function statusIcon(j) {
  if (j.status === "running") return `<span class="spin" style="color:var(--accent)">${icon("refresh")}</span>`;
  if (j.status === "queued") return `<span class="muted">${icon("clock")}</span>`;
  if (j.status === "error") return `<span style="color:var(--orange)">${icon("alert-triangle")}</span>`;
  if (j.status === "cancelled") return `<span class="muted">${icon("x")}</span>`;
  return `<span style="color:var(--accent)">${icon("circle-check")}</span>`;
}

// ---------------------------------------------------------------- список
function entries() {
  const grouped = new Set(groups.map((g) => g.title));
  const list = groups.map((g) => Object.assign({ created: g.created, finished: g.finished }, g))
    .concat(jobs.filter((j) => !grouped.has(j.title)));
  const active = (x) => x.status === "running" || x.status === "queued";
  const rank = (x) => (x.status === "running" ? 0 : x.status === "queued" ? 1 : 2);
  list.sort((a, b) => rank(a) - rank(b) || (b.finished || b.created) - (a.finished || a.created));
  if (filter === "active") return list.filter(active);
  if (filter === "errors") return list.filter((x) => x.status === "error" || (x.group && x.failed));
  return list;
}

function row(x) {
  const key = x.group ? `g:${x.title}` : x.id;
  const canCancel = x.status === "running" || x.status === "queued";
  const actions = `${canCancel ? `<button class="btn sm" data-cancel="${esc(key)}">Отменить</button>`
    : `<button class="btn icon sm" data-remove="${esc(key)}" aria-label="Убрать">${icon("x", "sm")}</button>`}`;
  const isOpen = open === key;
  return `<div class="li click" data-open="${esc(key)}">${statusIcon(x)}
      <div class="grow"><div class="ellip">${esc(title(x))}</div><div class="tiny muted ellip">${esc(detail(x))}</div></div>
      <span class="tiny muted hide-m">${esc(when(x))}</span>${actions}${icon(isOpen ? "chevron-down" : "chevron-right", "muted")}</div>
    ${isOpen ? `<div class="log" id="act-log">${x.group ? groupLog(x) : esc(logState.lines.join("\n")) || "…"}</div>` : ""}`;
}

function groupLog(g) {
  const members = jobs.filter((j) => j.title === g.title);
  if (!members.length) return "задачи пакета уже убраны из истории";
  return members.map((j) => `${{ done: "✓", error: "✗", running: "…", queued: "·", cancelled: "–" }[j.status] || "?"} ${esc(j.subject || j.name)}`).join("\n");
}

function listHtml() {
  const list = entries();
  if (!list.length) return `<div class="li"><div class="grow muted">${filter === "all" ? "Пока ничего не происходило" : "Таких задач нет"}</div></div>`;
  return list.map(row).join("") + (jobs.length >= limit
    ? `<div class="li" style="justify-content:center"><button class="btn sm" data-more>Показать более ранние</button></div>` : "");
}

function filters() {
  return `<div class="seg" style="margin-bottom:12px">${[["all", "Все"], ["active", "Идут"], ["errors", "С ошибками"]]
    .map(([k, l]) => `<button data-filter="${k}" class="${filter === k ? "on" : ""}">${l}</button>`).join("")}</div>`;
}

async function load() {
  const d = await api("GET", `/api/jobs?limit=${limit}`);
  jobs = d.jobs || [];
  groups = d.groups || [];
}

export async function render() {
  await load();
  return `<h1>Активность</h1><p class="sub">Что сервис делал — понятными словами; подробный лог по нажатию</p>
    <div id="act-filters">${filters()}</div><div class="card list" id="act-list">${listHtml()}</div>`;
}

// ---------------------------------------------------------------- лог
async function pullLog(root) {
  if (!open || open.startsWith("g:")) return;
  const id = open;
  try {
    const d = await api("GET", `/api/jobs/${id}?since=${logState.id === id ? logState.offset : 0}`);
    if (open !== id) return;
    const j = d.job;
    if (logState.id !== id) logState = { id, lines: [], offset: 0 };
    logState.lines = logState.lines.concat(j.log || []);
    logState.offset = (j.log_offset || 0) + (j.log || []).length;
    const el = root.querySelector("#act-log");
    if (el) {
      const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
      el.textContent = logState.lines.join("\n") || "лог пуст";
      if (atBottom) el.scrollTop = el.scrollHeight;
    }
    clearTimeout(logTimer);
    if (j.status === "running" || j.status === "queued") logTimer = setTimeout(() => pullLog(root), 1500);
  } catch (e) { /* задача могла уйти из истории */ }
}

function redraw(root) {
  // перерисовка при каждом опросе не должна сбивать прокрутку открытого лога
  const prev = root.querySelector("#act-log");
  const keep = prev ? { top: prev.scrollTop, bottom: prev.scrollTop + prev.clientHeight >= prev.scrollHeight - 20 } : null;
  root.querySelector("#act-filters").innerHTML = filters();
  root.querySelector("#act-list").innerHTML = listHtml();
  const el = root.querySelector("#act-log");
  if (el && logState.id === open) {
    el.textContent = logState.lines.join("\n") || "…";
    el.scrollTop = !keep || keep.bottom ? el.scrollHeight : keep.top;
  }
}

export function mount(root) {
  on(root, "click", "[data-filter]", (e, b) => { filter = b.dataset.filter; redraw(root); });
  on(root, "click", "[data-open]", (e, r) => {
    if (e.target.closest("button")) return;
    const key = r.dataset.open;
    open = open === key ? null : key;
    clearTimeout(logTimer);
    logState = { id: null, lines: [], offset: 0 };
    redraw(root);
    pullLog(root);
  });
  on(root, "click", "[data-cancel]", async (e, b) => {
    const key = b.dataset.cancel;
    try {
      if (key.startsWith("g:")) await api("POST", "/api/jobs-group/cancel", { title: key.slice(2) });
      else await api("POST", `/api/jobs/${key}/cancel`);
      toast("Отменяется — текущий шаг допишется");
      pokeSoon();
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-remove]", async (e, b) => {
    const key = b.dataset.remove;
    try {
      if (key.startsWith("g:")) await api("POST", "/api/jobs-group/remove", { title: key.slice(2) });
      else await api("DELETE", `/api/jobs/${key}`);
      if (open === key) open = null;
      await load();
      redraw(root);
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-more]", async () => {
    limit = Math.min(limit * 3, 1000);
    await load();
    redraw(root);
  });
  return () => { clearTimeout(logTimer); open = null; };
}

export function onState(root, st) {
  if (!st.jobs) return;
  if (limit <= 60) {                 // снимок очереди уже есть — без лишнего запроса
    jobs = st.jobs.jobs || [];
    groups = st.jobs.groups || [];
  }
  redraw(root);
}

