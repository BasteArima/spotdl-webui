// Главная: что происходит сейчас, источники, что требует решения, недавнее.
// Данные — /api/v2/home (одним запросом); «Сейчас» обновляется из опроса очереди.
import { api } from "../api.js";
import { describeNow } from "../store.js";
import { bar, cover, esc, fmtAgo, fmtIn, fmtNum, icon, on, plural, srcIcon, srcIcons } from "../ui.js";

let data = null;
let lastLibrary = null;

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

function fmtSize(b) {
  if (!b) return "";
  if (b >= 1e9) return `${(b / 1e9).toFixed(1).replace(".", ",")} ГБ`;
  return `${Math.round(b / 1e6)} МБ`;
}

function fmtDur(s) {
  if (!s) return "";
  s = Math.round(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ---------------------------------------------------------------- блоки
function nowRows(jobs) {
  const rows = [];
  const now = describeNow(jobs);
  if (now) {
    rows.push(`<a class="li click" href="#/activity" style="color:inherit">
      <span class="${now.spin ? "spin" : ""}" style="color:var(--accent)">${icon(now.icon)}</span>
      <div class="grow"><div class="ellip">${esc(now.title)}</div>
      ${now.pct != null ? `<div style="margin:6px 0 4px">${bar(now.pct)}</div>` : ""}
      <div class="tiny muted ellip">${esc(now.sub || "")}</div></div>${icon("chevron-right", "muted")}</a>`);
  }
  const u = jobs && jobs.upgrade;
  if (u && u.state && u.state !== "idle" && !(now && now.kind === "upgrade")) {
    const label = { running: "идёт", paused: "на паузе", stopping: "останавливается", error: "ошибка" }[u.state] || u.state;
    rows.push(`<a class="li click" href="#/quality" style="color:inherit">${icon("arrow-big-up-lines")}
      <div class="grow"><div>Улучшение до 320k · ${esc(label)}</div>
      <div class="tiny muted">${fmtNum(u.done + u.skipped + u.failed)} из ${fmtNum(u.total)} · улучшено ${fmtNum(u.upgraded_total || u.done)}</div></div>
      ${icon("chevron-right", "muted")}</a>`);
  }
  const next = jobs && jobs.next_autosync;
  const queued = jobs && jobs.queued;
  rows.push(`<div class="li">${icon("clock", "muted")}<div class="grow"><div>${next ? "Следующий автосинк" : "Автосинк выключен"}</div>
    <div class="tiny muted">${next ? esc(fmtIn(next - Date.now() / 1000)) : "включается в настройках"}${
      queued ? ` · в очереди ${queued}` : ""}</div></div></div>`);
  if (!now && jobs && jobs.last_sync) {
    const ls = jobs.last_sync;
    rows.unshift(`<div class="li">${icon("circle-check")}<div class="grow"><div>Всё спокойно</div>
      <div class="tiny muted">последний синк «${esc(ls.name)}» ${esc(fmtAgo(ls.ts))}${ls.downloaded ? `, +${ls.downloaded}` : ""}</div></div></div>`);
  }
  return rows.join("");
}

function sourceCard(platform, title, big, small, status, statusOk, href) {
  return `<a class="card pad" href="${href}" style="color:inherit;display:block">
    <div class="row" style="gap:8px;margin-bottom:10px">${srcIcon(platform)}<b style="font-weight:600" class="ellip">${title}</b></div>
    <div class="stat"><div class="n">${big}</div></div><div class="small muted">${small}</div>
    ${status ? `<div class="tiny" style="margin-top:8px;color:${statusOk ? "var(--accent)" : "var(--orange)"}">${icon(statusOk ? "circle-check" : "alert-circle", "sm")} ${esc(status)}</div>` : ""}
  </a>`;
}

function sources(d) {
  const s = d.sources, env = d.env;
  const sp = s.spotify, liked = sp.liked;
  const ytm = s.ytm;
  return `<div class="grid g3">
    ${sourceCard("spotify", "Spotify", `${sp.playlists} ${plural(sp.playlists, "плейлист", "плейлиста", "плейлистов")}`,
      liked ? `+ Любимые · ${fmtNum(liked.have)} из ${fmtNum(liked.total)}` : "Любимые не подключены",
      env.spotify_login ? `вход выполнен${env.spotify_user ? ": " + env.spotify_user : ""}` : (liked ? "нет входа — Любимые не синкаются" : ""),
      !!env.spotify_login, "#/playlists")}
    ${sourceCard("ytm", "YouTube Music", ytm.playlists || ytm.liked ? `${ytm.playlists} ${plural(ytm.playlists, "плейлист", "плейлиста", "плейлистов")}` : "Скоро",
      ytm.liked ? `+ Понравившиеся · ${fmtNum(ytm.liked.have)} из ${fmtNum(ytm.liked.total)}` : "синк плейлистов и «Понравившихся» — этап 4 плана",
      env.cookies ? "cookies загружены" : "нет cookies", !!env.cookies, "#/settings")}
    ${sourceCard("manual", "Вручную", `${fmtNum(s.manual.tracks)} ${plural(s.manual.tracks, "трек", "трека", "треков")}`,
      "файлы, Deezer, из поиска — вне плейлистов", "", true, "#/library")}
  </div>`;
}

function attention(d) {
  const a = d.attention, rows = [];
  const r = a.missing_by_reason || {};
  if (a.missing) {
    const parts = [];
    if (r.not_found) parts.push(`нет на YouTube ${r.not_found}`);
    if (r.download_failed) parts.push(`YouTube не отдал ${r.download_failed}`);
    if (r.network) parts.push(`сбой сети ${r.network}`);
    if (r.unavailable) parts.push(`недоступны ${r.unavailable}`);
    if (r.other) parts.push(`прочее ${r.other}`);
    rows.push(`<a class="li click" href="#/attention" style="color:inherit"><span style="color:var(--orange)">${icon("music-off")}</span>
      <div class="grow"><div>${fmtNum(a.missing)} ${plural(a.missing, "трек не скачался", "трека не скачались", "треков не скачались")}</div>
      <div class="tiny muted ellip">${esc(parts.join(" · "))} — можно взять с Deezer или Spotify напрямую</div></div>${icon("chevron-right", "muted")}</a>`);
  }
  if (a.dupes) {
    rows.push(`<a class="li click" href="#/attention" style="color:inherit"><span style="color:var(--orange)">${icon("copy")}</span>
      <div class="grow"><div>${fmtNum(a.dupes)} ${plural(a.dupes, "возможный дубль", "возможных дубля", "возможных дублей")}</div>
      <div class="tiny muted">одна запись лежит несколькими файлами — решите, склеивать ли</div></div>${icon("chevron-right", "muted")}</a>`);
  }
  (a.unavailable || []).forEach((u) => rows.push(`<a class="li click" href="#/attention" style="color:inherit">
    <span style="color:var(--red)">${icon("lock")}</span><div class="grow"><div>«${esc(u.name)}» недоступен</div>
    <div class="tiny muted">площадка не отдаёт этот плейлист</div></div>${icon("chevron-right", "muted")}</a>`));
  (d.env.warnings || []).forEach((w) => rows.push(`<a class="li click" href="#/settings" style="color:inherit">
    <span style="color:var(--orange)">${icon("alert-triangle")}</span><div class="grow"><div class="small">${esc(w)}</div></div>${icon("chevron-right", "muted")}</a>`));
  if (!rows.length) rows.push(`<div class="li">${icon("circle-check")}<div class="grow">Всё в порядке — решать нечего</div></div>`);
  return rows.join("");
}

function recent(d) {
  if (!d.recent.length) return `<div class="li"><div class="grow muted">Библиотека пока пуста</div></div>`;
  return d.recent.map((t) => `<div class="li">${cover(t.album || t.title)}
    <div class="grow"><div class="ellip">${esc(t.title)}</div><div class="tiny muted ellip">${esc([t.artist, t.album].filter(Boolean).join(" · "))}</div></div>
    ${srcIcons(t.platforms)}<span class="tiny muted hide-m">${esc(t.codec || "")}${t.bitrate ? " " + t.bitrate + "k" : ""}</span>
    <span class="tiny muted hide-m" style="width:36px;text-align:right">${fmtDur(t.duration)}</span></div>`).join("");
}

function subline(d) {
  const lib = d.library;
  const parts = [`В библиотеке ${fmtNum(lib.files)} ${plural(lib.files, "трек", "трека", "треков")}`];
  if (lib.size) parts.push(fmtSize(lib.size));
  parts.push(`${lib.playlists} ${plural(lib.playlists, "плейлист", "плейлиста", "плейлистов")}`);
  const nd = d.navidrome;
  if (nd && nd.configured) {
    parts.push(nd.last ? (nd.last.ok ? `Navidrome обновлён ${fmtAgo(nd.last.ts)}` : "Navidrome: последний скан не удался") : "Navidrome подключён");
  }
  return parts.join(" · ");
}

// ---------------------------------------------------------------- экран
export async function render(ctx) {
  data = await api("GET", "/api/v2/home");
  lastLibrary = ctx.state.library;
  return `<h1>${greeting()}</h1>
    <p class="sub" id="home-sub">${esc(subline(data))}</p>
    <a class="search" href="#/search" style="color:var(--text3);text-decoration:none">${icon("search")}<span class="ellip">Найти трек, альбом или исполнителя — в библиотеке и на площадках</span></a>
    <h2>Сейчас</h2><div class="card list" id="home-now">${nowRows(ctx.state.jobs)}</div>
    <h2>Источники</h2><div id="home-sources">${sources(data)}</div>
    <h2>Требует внимания</h2><div class="card list" id="home-attention">${attention(data)}</div>
    <h2>Недавно добавлено</h2><div class="card list" id="home-recent">${recent(data)}</div>`;
}

let refetching = false;
async function refetch(root) {
  if (refetching) return;
  refetching = true;
  try {
    data = await api("GET", "/api/v2/home");
    const set = (id, h) => { const el = root.querySelector(id); if (el) el.innerHTML = h; };
    set("#home-sub", esc(subline(data)));
    set("#home-sources", sources(data));
    set("#home-attention", attention(data));
    set("#home-recent", recent(data));
  } catch (e) { /* следующий снимок попробует снова */ }
  refetching = false;
}

export function onState(root, st) {
  const el = root.querySelector("#home-now");
  if (el) el.innerHTML = nowRows(st.jobs);
  // сводка библиотеки обновилась (очередь затихла) — перечитать главную
  if (st.library && st.library !== lastLibrary) {
    lastLibrary = st.library;
    refetch(root);
  }
}

export function mount(root) {
  on(root, "click", ".search", (e) => { e.preventDefault(); location.hash = "#/search"; });
}
