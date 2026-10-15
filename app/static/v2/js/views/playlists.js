// Плейлисты: список (#/playlists) и страница плейлиста (#/playlists/<id>).
// Данные — индекс библиотеки (/api/v2/playlists…), живой синк — снимок очереди.
import { api } from "../api.js";
import { bulkBar, missingRow, mountMissing, setCaps, updateMissingJobs } from "../missing.js";
import { pokeSoon } from "../store.js";
import { bar, chip, cover, esc, fmtAgo, fmtNum, icon, on, plural, srcIcon, srcIcons, toast } from "../ui.js";

let list = null;          // список плейлистов
let pl = null;            // открытый плейлист
let filter = "all";
let lastLibrary = null;

const fmtDur = (s) => (s ? `${Math.floor(Math.round(s) / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}` : "");

// ---------------------------------------------------------------- живой синк
function liveSync(st, legacyId) {
  return legacyId && st.jobs && st.jobs.syncs ? st.jobs.syncs[legacyId] : null;
}
function syncChip(live, p) {
  if (live && live.status === "running") {
    const pr = live.progress;
    const pct = pr && pr.total && pr.phase === "download" ? Math.round((pr.processed / pr.total) * 100) : null;
    return `<span class="chip info"><span class="spin">${icon("loader-2", "sm")}</span>${pct != null ? `синк ${pct}%` : "синк…"}</span>`;
  }
  if (live && live.status === "queued") return chip("в очереди");
  if (p.sources.some((s) => s.status === "unavailable")) return chip("недоступен", "err");
  if (p.missing) return chip(`${fmtNum(p.missing)} не скачано`, "warn");
  if (!p.total) return chip("ещё не синхронизирован");
  return chip("всё скачано", "ok");
}

function lastSyncText(ls) {
  if (!ls) return "";
  let t = "синк " + fmtAgo(ls.ts);
  if (ls.downloaded) t += `, +${ls.downloaded}`;
  return t;
}

// ---------------------------------------------------------------- список
function listRow(p, st) {
  const names = [...new Set(p.sources.map((s) => ({ spotify: "Spotify", ytm: "YouTube Music" }[s.platform] || "вручную")))].join(" + ");
  const sub = [names, p.total ? `${fmtNum(p.total)} ${plural(p.total, "трек", "трека", "треков")}` : "",
    p.multi ? `${p.multi} из нескольких источников` : "", lastSyncText(p.last_sync)].filter(Boolean).join(" · ");
  return `<a class="li click" href="#/playlists/${p.id}" style="color:inherit" data-pl="${p.id}">${cover(p.name, "md")}
    <div class="grow"><div class="ellip" style="font-weight:500">${esc(p.name)}</div><div class="tiny muted ellip">${esc(sub)}</div></div>
    ${srcIcons([...new Set(p.sources.map((s) => s.platform))])}<span data-chip="${p.id}">${syncChip(liveSync(st, p.legacy_id), p)}</span>
    <span class="hide-m">${icon("chevron-right", "muted")}</span></a>`;
}

function addForm() {
  return `<form class="card pad hidden" id="pl-add" style="margin-bottom:14px">
    <div style="font-weight:600;margin-bottom:8px">Новый плейлист</div>
    <div class="grid g2"><input class="field" name="name" placeholder="Название, например «Для работы»" required>
    <input class="field" name="url" placeholder="Ссылка Spotify или saved — Любимые" required></div>
    <div class="tiny muted" style="margin:8px 0 10px">Плейлист, альбом или исполнитель Spotify. Персональные подборки (Daily Mix, Discover Weekly) Spotify через API не отдаёт. YouTube Music — этап 4.</div>
    <div class="row" style="gap:8px"><button class="btn primary" type="submit">Добавить</button><button class="btn" type="button" data-add-cancel>Отмена</button></div>
  </form>`;
}

async function renderList(ctx) {
  list = (await api("GET", "/api/v2/playlists")).playlists;
  return `<div class="row" style="justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px">
      <div><h1>Плейлисты</h1><p class="sub">${list.length} ${plural(list.length, "плейлист", "плейлиста", "плейлистов")} · один плейлист может собираться из нескольких источников</p></div>
      <div class="row" style="gap:8px"><button class="btn" data-sync-all>${icon("refresh")}Синхронизировать всё</button>
      <button class="btn primary" data-add>${icon("plus")}Добавить</button></div></div>
    ${addForm()}
    <div class="card list" id="pl-list">${list.length ? list.map((p) => listRow(p, ctx.state)).join("")
      : `<div class="li"><div class="grow muted">Плейлистов пока нет — добавьте первый</div></div>`}</div>`;
}

// ---------------------------------------------------------------- страница плейлиста
function head(p, st) {
  const live = liveSync(st, p.legacy_id);
  const pr = live && live.status === "running" ? live.progress : null;
  const parts = [`${fmtNum(p.total)} ${plural(p.total, "трек", "трека", "треков")}`, `скачано ${fmtNum(p.have)}`, lastSyncText(p.last_sync)].filter(Boolean);
  return `<div class="tiny muted">ПЛЕЙЛИСТ</div><h1 style="margin:2px 0 6px">${esc(p.name)}</h1>
    <div class="small muted">${esc(parts.join(" · "))}</div>
    ${pr ? `<div style="margin-top:10px">${bar(pr.phase === "download" && pr.total ? (pr.processed / pr.total) * 100 : 0)}
      <div class="tiny muted" style="margin-top:4px">${pr.phase === "download" ? `${pr.processed} из ${pr.total}` : "собираю данные треков…"}${
        pr.current ? ` · сейчас: ${esc(pr.current)}` : ""}</div></div>` : ""}
    <div class="row" style="gap:8px;margin-top:12px;flex-wrap:wrap">
      <button class="btn primary" data-sync ${live ? "disabled" : ""}>${icon("refresh")}${live ? (live.status === "running" ? "Синк идёт" : "В очереди") : "Синхронизировать"}</button>
      <button class="btn" data-rename>${icon("file-text")}Переименовать</button>
      <button class="btn" data-delete>${icon("trash")}Удалить</button></div>`;
}

function stateCard(p) {
  const pct = p.total ? (p.have / p.total) * 100 : 0;
  const ls = p.last_sync;
  const fails = [];
  if (ls) {
    if (ls.lookup_failed) fails.push(`не найдено ${ls.lookup_failed}`);
    if (ls.download_failed) fails.push(`YouTube не отдал ${ls.download_failed}`);
  }
  return `<div class="card pad"><div style="font-weight:600">Состояние</div>
    <div class="tiny muted" style="margin:2px 0 10px">Звук для синка берётся с YouTube Music; чего там нет — ниже, с выбором площадки.</div>
    ${bar(pct)}<div class="row small" style="justify-content:space-between;margin-top:8px"><span>${Math.round(pct)}% скачано</span>
    <span class="muted">${p.missing ? `${fmtNum(p.missing)} не скачано` : "всё на месте"}</span></div>
    ${ls ? `<div class="small" style="margin-top:12px">${icon(ls.ok ? "circle-check" : "alert-triangle", "sm")} последний синк ${esc(fmtAgo(ls.ts))}: скачано ${ls.downloaded || 0}, уже было ${ls.skipped || 0}${fails.length ? " · " + esc(fails.join(" · ")) : ""}</div>` : ""}
    ${ls && ls.yt_reason ? `<div class="small" style="margin-top:6px;color:var(--orange)">${icon("alert-triangle", "sm")} ${esc(ls.yt_reason)}</div>` : ""}
    ${p.multi ? `<div class="small" style="margin-top:8px">${icon("link", "sm")} ${p.multi} ${plural(p.multi, "трек пришёл", "трека пришли", "треков пришли")} из нескольких источников — файл у каждого один</div>` : ""}
  </div>`;
}

function sourcesCard(p) {
  return `<div class="card pad"><div style="font-weight:600">Откуда список треков</div>
    <div class="tiny muted" style="margin:2px 0 8px">Сервис следит за этими плейлистами площадок и добавляет новые треки. Если трек есть в нескольких — файл один.</div>
    ${p.sources.map((s) => `<div class="row" style="padding:6px 0">${srcIcon(s.platform)}<div class="grow" style="min-width:0">
      <div>${esc(s.label)}</div><div class="tiny muted ellip">${s.ref === "saved" ? "ваши «Любимые» в Spotify"
        : `<a href="${esc(s.ref)}" target="_blank" rel="noopener">${esc(s.ref.replace(/^https?:\/\//, "").replace(/\?.*$/, ""))}</a>`}</div></div>
      ${s.status === "unavailable" ? chip("недоступен", "err") : ""}</div>`).join("")}
    <div class="tiny muted" style="margin-top:8px">${icon("plus", "sm")} Второй источник (плейлист YouTube Music) — этап 4.</div></div>`;
}

function trackRow(t) {
  const right = t.status === "missing" ? chip("не скачан", "warn") : `<span class="tiny muted hide-m">${esc(t.codec || "")}${t.bitrate ? " " + t.bitrate + "k" : ""}</span>`;
  return `<div class="li">${cover(t.album || t.title)}<div class="grow"><div class="ellip">${esc(t.title)}</div>
    <div class="tiny muted ellip">${esc([t.artist, t.album].filter(Boolean).join(" · "))}</div></div>
    ${srcIcons(t.platforms)}${right}<span class="tiny muted hide-m" style="width:36px;text-align:right">${fmtDur(t.duration)}</span></div>`;
}

function tracksBlock(p) {
  let rows = p.tracks;
  if (filter === "missing") rows = rows.filter((t) => t.status === "missing");
  if (filter === "multi") rows = rows.filter((t) => t.platforms.length > 1);
  const segs = [["all", `Все · ${p.tracks.length}`], ["missing", `Не скачаны · ${p.missing}`]];
  if (p.sources.length > 1) segs.push(["multi", `Из нескольких · ${p.multi}`]);
  return `<div class="row" style="justify-content:space-between;margin:24px 0 10px;flex-wrap:wrap;gap:10px"><h2 style="margin:0">Треки</h2>
    <div class="seg" style="max-width:100%;overflow-x:auto">${segs.map(([k, l]) => `<button data-filter="${k}" class="${filter === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="card list">${rows.length ? rows.map(trackRow).join("") : `<div class="li"><div class="grow muted">Пусто</div></div>`}</div>`;
}

function missingBlock(p) {
  const miss = p.tracks.filter((t) => t.status === "missing");
  if (!miss.length) return "";
  const fetchable = miss.filter((t) => t.spotify_url).length;
  return `<h2>Не скачано · ${miss.length}</h2><div id="pl-bulk">${fetchable ? bulkBar(fetchable) : ""}</div>
    <div class="card list">${miss.map((t) => missingRow(t)).join("")}</div>`;
}

async function renderOne(ctx) {
  pl = await api("GET", `/api/v2/playlists/${encodeURIComponent(ctx.param)}`);
  setCaps(pl.caps);
  filter = "all";
  return `<a class="back" href="#/playlists">${icon("chevron-left")}Плейлисты</a>
    <div class="pl-head">${cover(pl.name, "lg")}<div class="grow" id="pl-head">${head(pl, ctx.state)}</div></div>
    <div class="grid g2">${sourcesCard(pl)}<div id="pl-state">${stateCard(pl)}</div></div>
    <div id="pl-missing">${missingBlock(pl)}</div>
    <div id="pl-tracks">${tracksBlock(pl)}</div>`;
}

// ---------------------------------------------------------------- экран
export async function render(ctx) {
  lastLibrary = ctx.state.library;
  return ctx.param ? renderOne(ctx) : renderList(ctx);
}

function sourceUrl(p) {
  // для синка и правок записи playlists.txt — ссылка первого источника (до этапа 3)
  const s = p.sources[0];
  return s ? s.ref : null;
}

export function mount(root, ctx) {
  if (!ctx.param) {
    on(root, "click", "[data-add]", () => { const f = root.querySelector("#pl-add"); f.classList.toggle("hidden"); f.querySelector("input").focus(); });
    on(root, "click", "[data-add-cancel]", () => root.querySelector("#pl-add").classList.add("hidden"));
    on(root, "submit", "#pl-add", async (e, form) => {
      e.preventDefault();
      try {
        await api("POST", "/api/playlists", { name: form.name.value.trim(), url: form.url.value.trim() });
        toast("Добавлен — синкните его, чтобы скачать треки");
        form.reset();
        form.classList.add("hidden");
        pokeSoon();
      } catch (ex) { toast(ex.message); }
    });
    on(root, "click", "[data-sync-all]", async () => {
      try { await api("POST", "/api/sync-all"); toast("Синк всех плейлистов в очереди"); pokeSoon(); }
      catch (ex) { toast(ex.message); }
    });
    return null;
  }
  // страница плейлиста
  on(root, "click", "[data-sync]", async () => {
    const url = sourceUrl(pl);
    if (!url) return;
    try { await api("POST", "/api/sync", { url }); toast("Синк в очереди"); pokeSoon(); }
    catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-rename]", async () => {
    const name = prompt("Новое название плейлиста", pl.name);
    if (!name || name.trim() === pl.name) return;
    try {
      await api("PUT", "/api/playlists", { old_url: sourceUrl(pl), name: name.trim(), url: sourceUrl(pl) });
      toast("Переименован");
      pokeSoon();
      pl.name = name.trim();
      root.querySelector("#pl-head").innerHTML = head(pl, ctx.state);
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-delete]", async () => {
    if (!confirm(`Удалить плейлист «${pl.name}»? Скачанные файлы останутся в библиотеке.`)) return;
    try {
      await api("DELETE", "/api/playlists", { url: sourceUrl(pl) });
      toast("Плейлист удалён, файлы остались");
      pokeSoon();
      location.hash = "#/playlists";
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-filter]", (e, b) => {
    filter = b.dataset.filter;
    root.querySelector("#pl-tracks").innerHTML = tracksBlock(pl);
  });
  mountMissing(root, {
    getTracks: () => (pl ? pl.tracks : []),
    playlistId: pl.id,
    bulkUrl: `/api/v2/playlists/${pl.id}/fetch-missing`,
    rerenderBulk: () => {
      const n = pl.tracks.filter((t) => t.status === "missing" && t.spotify_url).length;
      root.querySelector("#pl-bulk").innerHTML = n ? bulkBar(n) : "";
    },
  });
  return null;
}

let refetching = false;
async function refetch(root, ctx) {
  if (refetching) return;
  refetching = true;
  try {
    if (!ctx.param) {
      list = (await api("GET", "/api/v2/playlists")).playlists;
      const el = root.querySelector("#pl-list");
      if (el) el.innerHTML = list.map((p) => listRow(p, ctx.state)).join("");
    } else {
      pl = await api("GET", `/api/v2/playlists/${encodeURIComponent(ctx.param)}`);
      setCaps(pl.caps);
      const set = (id, h) => { const el = root.querySelector(id); if (el) el.innerHTML = h; };
      set("#pl-head", head(pl, ctx.state));
      set("#pl-state", stateCard(pl));
      set("#pl-missing", missingBlock(pl));
      set("#pl-tracks", tracksBlock(pl));
    }
  } catch (e) { /* следующая попытка — со следующим снимком */ }
  refetching = false;
}

let ctxRef = null;
export function onState(root, st) {
  const param = (location.hash.split("/")[2] || "") || null;
  ctxRef = { param, state: st };
  if (!param && list) {
    list.forEach((p) => {
      const el = root.querySelector(`[data-chip="${p.id}"]`);
      if (el) el.innerHTML = syncChip(liveSync(st, p.legacy_id), p);
    });
  } else if (param && pl) {
    const h = root.querySelector("#pl-head");
    if (h) h.innerHTML = head(pl, st);
    updateMissingJobs(root, st, () => pl.tracks);
  }
  if (st.library && st.library !== lastLibrary) {
    lastLibrary = st.library;
    refetch(root, ctxRef);
  }
}
