// Живое состояние сервиса: снимок очереди (/api/jobs) и сводка библиотеки
// (/api/library). Опрос — часто, пока что-то идёт; редко в простое; на
// скрытой вкладке не опрашиваем вовсе. Экраны подписываются через subscribe.

import { api } from "./api.js";

export const state = { jobs: null, library: null };
const listeners = new Set();
let timer = null;
let lastActive = -1;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function emit() {
  listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
}

function isBusy(j) {
  return j && (j.active > 0 || (j.upgrade && j.upgrade.state === "running"));
}

export async function refreshLibrary() {
  try {
    state.library = await api("GET", "/api/library");
    emit();
  } catch (e) { /* сводка необязательна */ }
}

async function tick() {
  clearTimeout(timer);
  if (document.hidden) return;              // вернёмся по visibilitychange
  try {
    state.jobs = await api("GET", "/api/jobs?limit=60");
    // очередь только что затихла — пересчитать сводку библиотеки (новые файлы, «не скачано»)
    if (lastActive > 0 && state.jobs.active === 0) refreshLibrary();
    lastActive = state.jobs.active;
    emit();
  } catch (e) {
    if (e.status === 401) return;           // экран входа покажет main.js
  }
  timer = setTimeout(tick, isBusy(state.jobs) ? 1500 : 15000);
}

export function startPolling() {
  tick();
  refreshLibrary();
}
export function stopPolling() {
  clearTimeout(timer);
}
export function pokeSoon() {
  // после действия пользователя (поставили синк) — не ждать 15 с
  clearTimeout(timer);
  timer = setTimeout(tick, 400);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) tick();
});

// ---------------------------------------------------------------- «что сейчас происходит»
// Одна строка для меню и главной: синк с прогрессом, загрузки, индекс, апгрейд, иначе покой.
export function describeNow(j) {
  if (!j) return null;
  const running = (j.jobs || []).filter((x) => x.status === "running");
  const sync = running.find((x) => x.progress);
  if (sync) {
    const p = sync.progress;
    const dl = p.phase === "download" && p.total;
    return {
      kind: "sync", icon: "refresh", spin: true,
      title: `Синк «${p.name}»`,
      sub: dl ? `${p.processed} из ${p.total}` + (p.eta_seconds != null ? ` · ещё ~${Math.max(1, Math.round(p.eta_seconds / 60))} мин` : "")
        : p.phase === "list" ? "получаю список из Spotify…" : "собираю данные треков…",
      pct: dl ? (p.processed / p.total) * 100 : null, jobId: sync.id,
    };
  }
  const group = (j.groups || []).find((g) => g.running > 0 || g.queued > 0);
  if (group) {
    return { kind: "batch", icon: "download", spin: false, title: `Загрузка «${group.name}»`,
      sub: `${group.done + group.failed} из ${group.total}`, pct: ((group.done + group.failed) / group.total) * 100 };
  }
  const idx = running.find((x) => x.kind === "index");
  if (idx) return { kind: "index", icon: "refresh", spin: true, title: "Обновляю индекс библиотеки", sub: "читаю теги файлов", jobId: idx.id };
  const one = running[0];
  if (one) return { kind: "job", icon: "download", spin: false, title: one.subject || one.name || one.title, sub: "загружается", jobId: one.id };
  const u = j.upgrade;
  if (u && u.state === "running") {
    return { kind: "upgrade", icon: "arrow-big-up-lines", spin: false, title: "Улучшение до 320k",
      sub: `${u.done + u.skipped + u.failed} из ${u.total}`, pct: u.total ? ((u.done + u.skipped + u.failed) / u.total) * 100 : null };
  }
  return null;
}
