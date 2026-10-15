// Недокачанные треки: выбор источника на трек и на все сразу, своя ссылка
// YouTube, свой файл, статус загрузки прямо в строке. Используется страницей
// плейлиста и «Вниманием».
import { api } from "./api.js";
import { pokeSoon } from "./store.js";
import { cover, esc, icon, on, toast } from "./ui.js";

export const SOURCES = [
  ["auto", "Авто"], ["deezer", "Deezer"], ["spotify", "Spotify 320k"],
  ["youtube", "Своя ссылка YouTube"], ["file", "Свой файл"],
];

export const REASON = {
  not_found: "нет на YouTube Music",
  download_failed: "YouTube не отдал файл",
  network: "сбой сети — повторится при синке",
  unavailable: "недоступен на площадке",
  other: "файла нет в библиотеке",
};

const picks = new Map();      // трек → выбранный источник
const urls = new Map();       // трек → введённая ссылка YouTube
const jobs = new Map();       // трек → id задачи загрузки
let caps = { deezer: false, spotify: false };

export function setCaps(c) { caps = c || caps; }

function defaultPick(t) {
  if (t.missing && t.missing.reason === "download_failed" && t.missing.source_url) return "youtube";
  return "auto";
}
const pickOf = (t) => picks.get(t.id) || defaultPick(t);

function mins(d) { return d ? `~${Math.max(1, Math.round(d / 60))} мин` : "минуты"; }

function hint(t, pick) {
  switch (pick) {
    case "auto":
      if (caps.deezer && caps.spotify) return "Deezer по ISRC, не выйдет — Spotify 320k";
      if (caps.deezer) return "Deezer по ISRC (Spotify напрямую не настроен)";
      if (caps.spotify) return `Spotify 320k со скоростью прослушивания, ${mins(t.duration)} (Deezer не настроен)`;
      return "нет источников — настройте Deezer или Spotify напрямую в настройках";
    case "deezer":
      if (!caps.deezer) return "Deezer не настроен — нужен ARL-токен в настройках";
      return t.isrc ? "точное совпадение по ISRC · секунды" : "у трека нет ISRC — поиск по названию";
    case "spotify":
      if (!caps.spotify) return "Spotify напрямую не настроен — войдите в настройках";
      return `прямо со Spotify · 320k · со скоростью прослушивания, ${mins(t.duration)}`;
    default:
      return "";
  }
}

function controls(t) {
  const pick = pickOf(t);
  const opts = SOURCES.map(([k, l]) => {
    const off = (k === "deezer" && !caps.deezer) || (k === "spotify" && !caps.spotify);
    return `<option value="${k}" ${k === pick ? "selected" : ""} ${off ? "disabled" : ""}>${l}${off ? " — не настроен" : ""}</option>`;
  }).join("");
  const btn = pick === "file"
    ? `<label class="btn sm tinted">${icon("upload", "sm")}Выбрать файл<input type="file" data-file="${t.id}" accept="audio/*,.mp3,.flac,.m4a,.opus,.ogg,.wav" hidden></label>`
    : `<button class="btn sm tinted" data-fetch="${t.id}">${icon("download", "sm")}Скачать</button>`;
  let extra;
  if (pick === "youtube") {
    const val = urls.get(t.id) ?? (t.missing && t.missing.source_url) ?? "";
    const q = encodeURIComponent(`${t.artist} ${t.title}`);
    extra = `<div class="row miss-hint" style="gap:10px;flex-wrap:wrap">
      <input class="field" data-yt="${t.id}" style="max-width:360px;height:30px;flex:1;min-width:180px" placeholder="https://music.youtube.com/watch?v=…" value="${esc(val)}">
      <a class="tiny" href="https://music.youtube.com/search?q=${q}" target="_blank" rel="noopener">искать на YouTube ↗</a></div>`;
  } else if (pick === "file") {
    extra = `<div class="tiny muted miss-hint">свой mp3 / flac / m4a / opus — теги и обложка подтянутся со Spotify</div>`;
  } else {
    extra = `<div class="tiny muted miss-hint">${esc(hint(t, pick))}</div>`;
  }
  return `<div class="miss-actions"><select class="select" data-pick="${t.id}">${opts}</select>${btn}</div>${extra}`;
}

function statusHtml(job) {
  if (!job) return "";
  if (job.status === "queued") return `<span class="chip">${icon("clock", "sm")} в очереди</span>`;
  if (job.status === "running") return `<span class="chip info"><span class="spin">${icon("loader-2", "sm")}</span> качаю…</span>`;
  if (job.status === "done") return `<span class="chip ok">${icon("check", "sm")} в библиотеке</span>`;
  return "";
}

export function missingRow(t, opts = {}) {
  const sub = [t.artist, opts.showPlaylist && t.playlist ? `«${t.playlist}»` : "", REASON[(t.missing || {}).reason] || ""]
    .filter(Boolean).join(" · ");
  return `<div class="li" style="flex-wrap:wrap" data-row="${t.id}">${cover(t.album || t.title)}
    <div class="grow"><div class="ellip">${esc(t.title)}</div><div class="tiny muted ellip">${esc(sub)}</div></div>
    <div class="miss-slot" data-slot="${t.id}">${rowBody(t)}</div></div>`;
}

function rowBody(t, job) {
  if (!t.spotify_url) {
    return `<span class="chip">нет ссылки Spotify</span>`;
  }
  if (job && job.status !== "error") return `<div class="miss-actions">${statusHtml(job)}</div>`;
  const err = job && job.status === "error" ? `<div class="tiny miss-hint" style="color:var(--red)">не вышло — попробуйте другой источник (подробности в «Активности»)</div>` : "";
  return controls(t) + err;
}

export function bulkBar(n) {
  const opts = [["auto", "Авто"], ["deezer", "Deezer"], ["spotify", "Spotify 320k"]];
  const cur = picks.get("bulk") || "auto";
  const text = {
    auto: caps.deezer || caps.spotify ? "для каждого трека — Deezer по ISRC, иначе Spotify 320k" : "нет источников — настройте Deezer или Spotify напрямую",
    deezer: caps.deezer ? "только Deezer: чего там нет, останется в списке" : "Deezer не настроен",
    spotify: caps.spotify ? "только Spotify 320k: медленно, со скоростью прослушивания и паузами" : "Spotify напрямую не настроен",
  }[cur];
  return `<div class="card pad row" style="flex-wrap:wrap;gap:10px;margin-bottom:10px" data-bulk>
    <div class="grow" style="min-width:220px"><div style="font-weight:600">Скачать все недостающие</div><div class="tiny muted">${esc(text)}</div></div>
    <div class="seg">${opts.map(([k, l]) => `<button data-bulk-pick="${k}" class="${cur === k ? "on" : ""}">${l}</button>`).join("")}</div>
    <button class="btn primary" data-bulk-go>${icon("download")}Скачать ${n}</button></div>`;
}

// события блока. tracks — текущие строки (для перерисовки), playlistId — контекст m3u
export function mountMissing(root, { getTracks, playlistId = null, bulkUrl = null, rerenderBulk }) {
  const find = (id) => getTracks().find((t) => String(t.id) === String(id));
  const redraw = (t) => {
    const slot = root.querySelector(`[data-slot="${t.id}"]`);
    if (slot) slot.innerHTML = rowBody(t, jobState(t.id));
  };
  async function fetchOne(t, body) {
    try {
      const r = await api("POST", `/api/v2/tracks/${t.id}/fetch`, Object.assign({ playlist_id: playlistId }, body));
      jobs.set(t.id, r.job.id);
      lastJobs.set(r.job.id, r.job);
      redraw(t);
      pokeSoon();
    } catch (e) { toast(e.message); }
  }
  on(root, "change", "[data-pick]", (e, sel) => {
    const t = find(sel.dataset.pick);
    if (!t) return;
    picks.set(t.id, sel.value);
    redraw(t);
  });
  on(root, "input", "[data-yt]", (e, inp) => urls.set(Number(inp.dataset.yt), inp.value));
  on(root, "click", "[data-fetch]", (e, b) => {
    const t = find(b.dataset.fetch);
    if (!t) return;
    const pick = pickOf(t);
    if (pick === "youtube") {
      const url = (urls.get(t.id) ?? (t.missing && t.missing.source_url) ?? "").trim();
      if (!url) { toast("Вставьте ссылку на ролик YouTube"); return; }
      fetchOne(t, { source: "youtube", url });
    } else {
      fetchOne(t, { source: pick });
    }
  });
  on(root, "change", "[data-file]", async (e, inp) => {
    const t = find(inp.dataset.file);
    if (!t || !inp.files || !inp.files[0]) return;
    const fd = new FormData();
    fd.append("file", inp.files[0]);
    if (playlistId) fd.append("playlist_id", playlistId);
    try {
      const token = localStorage.getItem("spotdl_webui_token") || "";
      const res = await fetch(`/api/v2/tracks/${t.id}/upload`, { method: "POST", body: fd,
        headers: token ? { Authorization: "Bearer " + token } : {} });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Ошибка ${res.status}`);
      jobs.set(t.id, data.job.id);
      lastJobs.set(data.job.id, data.job);
      redraw(t);
      pokeSoon();
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-bulk-pick]", (e, b) => {
    picks.set("bulk", b.dataset.bulkPick);
    if (rerenderBulk) rerenderBulk();
  });
  on(root, "click", "[data-bulk-go]", async (e, b) => {
    if (!bulkUrl) return;
    b.disabled = true;
    try {
      const r = await api("POST", bulkUrl, { source: picks.get("bulk") || "auto" });
      // сервер отдаёт явное соответствие «трек → задача» (порядок строк тут ни при чём)
      Object.entries(r.jobs || {}).forEach(([tid, jid]) => jobs.set(Number(tid), jid));
      getTracks().filter((t) => jobs.has(t.id)).forEach(redraw);
      toast(`В очереди ${r.queued}` + (r.skipped ? ` · без ссылки Spotify ${r.skipped}` : ""));
      pokeSoon();
    } catch (ex) { toast(ex.message); }
    b.disabled = false;
  });
}

// последнее известное состояние задач (снимок очереди)
const lastJobs = new Map();
function jobState(trackId) {
  const id = jobs.get(trackId);
  return id ? lastJobs.get(id) || { status: "queued" } : null;
}

export function updateMissingJobs(root, st, getTracks) {
  if (!st.jobs) return;
  (st.jobs.jobs || []).forEach((j) => lastJobs.set(j.id, j));
  getTracks().forEach((t) => {
    if (!jobs.has(t.id)) return;
    const slot = root.querySelector(`[data-slot="${t.id}"]`);
    if (slot) slot.innerHTML = rowBody(t, jobState(t.id));
  });
}
