// «Внимание»: то, что сервис не может решить сам. Вкладки — в адресе:
// #/attention (не скачано), #/attention/dupes, #/attention/sources.
import { api } from "../api.js";
import { bulkBar, missingRow, mountMissing, setCaps, updateMissingJobs } from "../missing.js";
import { pokeSoon, refreshLibrary } from "../store.js";
import { chip, cover, esc, fmtNum, icon, on, plural, srcIcons, toast } from "../ui.js";

let data = null;
let lastLibrary = null;
let plFilter = "";            // id плейлиста во «Не скачано» ("" — все)
let dupeLevel = "sure";       // sure — уровни 1–3, maybe — 4
let dupeShown = 30;
const DUPE_PAGE = 30;
const TABS = [["missing", "Не скачано"], ["dupes", "Дубли"], ["sources", "Источники"]];
const LEVEL = { 1: "тот же ролик или трек", 2: "тот же ISRC", 3: "название, исполнитель и длительность", 4: "под вопросом" };

const tabOf = (param) => (TABS.some(([k]) => k === param) ? param : "missing");
const fmtDur = (s) => (s ? `${Math.floor(Math.round(s) / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}` : "—");

function counts(d) {
  return { missing: d.missing.length, dupes: d.dupes.length, sources: d.unavailable.length };
}

function tabs(active, d) {
  const c = counts(d);
  return `<div class="seg" style="margin-bottom:16px;max-width:100%;overflow-x:auto">${TABS.map(([k, l]) =>
    `<a href="#/attention/${k}" class="${active === k ? "on" : ""}" style="color:inherit;text-decoration:none;padding:5px 14px;border-radius:7px;font-size:13px;font-weight:500;white-space:nowrap">${l} · ${fmtNum(c[k])}</a>`).join("")}</div>`;
}

// ---------------------------------------------------------------- не скачано
function playlistsOf(d) {
  // плейлисты в порядке появления (сервер сортирует по позиции)
  const seen = new Map();
  d.missing.forEach((t) => (t.playlists || []).forEach((p) => {
    const e = seen.get(p.id) || { id: p.id, name: p.name, n: 0 };
    e.n += 1;
    seen.set(p.id, e);
  }));
  return [...seen.values()];
}

function visibleMissing(d) {
  if (!plFilter) return d.missing;
  return d.missing.filter((t) => (t.playlists || []).some((p) => String(p.id) === plFilter));
}

function missingTab(d) {
  if (!d.missing.length) {
    return `<div class="card empty">${icon("circle-check", "xl")}<div style="margin-top:8px">Всё скачано</div></div>`;
  }
  const pls = playlistsOf(d);
  if (plFilter && !pls.some((p) => String(p.id) === plFilter)) plFilter = "";
  const list = visibleMissing(d);
  const fetchable = list.filter((t) => t.spotify_url).length;
  const picker = `<div class="row" style="gap:10px;margin-bottom:10px;flex-wrap:wrap">
    <select class="select" id="att-pl" style="height:32px;max-width:100%">
      <option value="">Все плейлисты · ${fmtNum(d.missing.length)}</option>
      ${pls.map((p) => `<option value="${p.id}" ${String(p.id) === plFilter ? "selected" : ""}>${esc(p.name)} · ${p.n}</option>`).join("")}
    </select>${plFilter ? `<a class="small" href="#/playlists/${plFilter}">открыть плейлист →</a>` : ""}</div>`;

  let rows;
  if (plFilter) {
    rows = list.map((t) => missingRow(t)).join("");
  } else {
    // все плейлисты — группами; трек из нескольких плейлистов — в первом из них
    const groups = new Map();
    list.forEach((t) => {
      const p = (t.playlists && t.playlists[0]) || { id: 0, name: "Без плейлиста" };
      if (!groups.has(p.id)) groups.set(p.id, { p, items: [] });
      groups.get(p.id).items.push(t);
    });
    rows = [...groups.values()].map(({ p, items }) => `<div class="pl-group">${icon("playlist", "sm")}
      <span class="grow ellip">${esc(p.name)}</span><span class="muted">${items.length}</span>
      ${p.id ? `<button class="btn sm" data-pl-filter="${p.id}">Только этот</button>` : ""}</div>
      ${items.map((t) => missingRow(t)).join("")}`).join("");
  }
  return `${picker}<div id="att-bulk">${fetchable ? bulkBar(fetchable) : ""}</div>
    <div class="card list">${rows}</div>
    <p class="tiny muted" style="margin-top:10px">Синк плейлистов берёт звук с YouTube Music. Сюда попадает то, чего там не нашлось или не скачалось, — у каждого трека можно выбрать, откуда его взять.</p>`;
}

// ---------------------------------------------------------------- дубли
function side(t) {
  const meta = [t.artist, t.album, fmtDur(t.duration), t.codec ? `${t.codec}${t.bitrate ? " " + t.bitrate + "k" : ""}` : ""].filter(Boolean).join(" · ");
  return `<div class="row" style="gap:10px">${cover(t.album || t.title)}<div class="grow" style="min-width:0">
      <div class="ellip">${esc(t.title)}</div><div class="tiny muted ellip">${esc(meta)}</div>
      ${t.playlists && t.playlists.length ? `<div class="tiny muted ellip">в плейлистах: ${esc(t.playlists.join(", "))}</div>` : ""}</div>
      ${srcIcons(t.platforms)}</div>
    <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
      <button class="btn sm" data-listen="${t.id}">${icon("headphones", "sm")}Слушать</button>
      <details class="tiny muted"><summary style="cursor:pointer">файл</summary><code style="word-break:break-all">${esc(t.path || "")}</code></details></div>
    <div data-player="${t.id}"></div>`;
}

function better(a, b) {
  return (a.bitrate || 0) >= (b.bitrate || 0) ? a : b;
}

function dupeCard(p) {
  const keep = better(p.a, p.b);
  return `<div class="card pad" style="margin-bottom:12px" data-dupe="${p.id}">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;gap:8px;flex-wrap:wrap">
      <span class="small muted">${esc(p.reason)}</span>${chip(`${LEVEL[p.level] || ""} · ${p.score}%`, p.level <= 3 ? "ok" : "warn")}</div>
    <div class="dup"><div class="side-a">${side(p.a)}</div><span class="vs muted">${icon("arrows-left-right")}</span><div class="side-b">${side(p.b)}</div></div>
    <div class="row" style="gap:8px;margin-top:12px;flex-wrap:wrap">
      <button class="btn primary sm" data-decide="same">${icon("link", "sm")}Это один трек — оставить ${esc(keep.codec || "")} ${keep.bitrate ? keep.bitrate + "k" : ""}</button>
      <button class="btn sm" data-decide="different">Разные треки</button></div></div>`;
}

function dupesTab(d) {
  const sure = d.dupes.filter((p) => p.level <= 3);
  const maybe = d.dupes.filter((p) => p.level > 3);
  const list = dupeLevel === "maybe" ? maybe : sure;
  const note = `<div class="card pad row" style="gap:10px;margin-bottom:12px">${icon("info-circle", "muted")}<div class="small muted">
    «Это один трек» пока запоминается: склеивать файлы безопасно, когда плейлисты для Navidrome собирает сам сервис —
    это следующий этап, тогда отмеченные пары склеятся сами (лишний файл — в корзину, не удаляется).${
    d.dupes_pending_merge ? ` Уже отмечено: ${fmtNum(d.dupes_pending_merge)}.` : ""}</div></div>`;
  const seg = `<div class="row" style="gap:10px;margin-bottom:12px;flex-wrap:wrap">
    <div class="seg"><button data-dlevel="sure" class="${dupeLevel === "sure" ? "on" : ""}">Уверенные · ${fmtNum(sure.length)}</button>
      <button data-dlevel="maybe" class="${dupeLevel === "maybe" ? "on" : ""}">Под вопросом · ${fmtNum(maybe.length)}</button></div>
    ${dupeLevel === "sure" && sure.length ? `<button class="btn tinted sm" data-bulk-same>${icon("link", "sm")}Отметить все уверенные как один трек</button>` : ""}</div>
    <div class="tiny muted" style="margin:-4px 4px 12px">${dupeLevel === "sure"
      ? "Уверенные: тот же ролик YouTube или трек Spotify, тот же ISRC, либо совпали название, исполнитель и длительность."
      : "Под вопросом: похоже, но не точно — лучше послушать обе версии."}</div>`;
  if (!list.length) {
    return note + seg + `<div class="card empty">${icon("circle-check", "xl")}<div style="margin-top:8px">Здесь пусто</div></div>`;
  }
  const shown = list.slice(0, dupeShown);
  return note + seg + `<div id="att-dupes">${shown.map(dupeCard).join("")}</div>${list.length > shown.length
    ? `<div style="text-align:center;margin:8px 0 20px"><button class="btn" data-dmore>Показать ещё (${fmtNum(list.length - shown.length)})</button></div>` : ""}`;
}

// ---------------------------------------------------------------- источники
function sourcesTab(d) {
  if (!d.unavailable.length) {
    return `<div class="card empty">${icon("circle-check", "xl")}<div style="margin-top:8px">Все источники отвечают</div></div>`;
  }
  return `<div class="card list">${d.unavailable.map((u) => `<div class="li" data-src="${u.id}">${cover(u.name, "md")}
    <div class="grow"><div style="font-weight:500">${esc(u.name)}</div>
    <div class="tiny muted">Spotify не отдаёт этот плейлист через API — так бывает с персональными подборками (Daily Mix, Discover Weekly, Release Radar) и закрытыми плейлистами. Сохраните нужные треки в свой плейлист — он синкается.</div></div>
    <button class="btn sm" data-remove="${esc(u.ref)}" data-name="${esc(u.name)}">Убрать</button></div>`).join("")}</div>`;
}

// ---------------------------------------------------------------- экран
function body(tab, d) {
  if (tab === "dupes") return dupesTab(d);
  if (tab === "sources") return sourcesTab(d);
  return missingTab(d);
}

export async function render(ctx) {
  data = await api("GET", "/api/v2/attention");
  setCaps(data.caps);
  lastLibrary = ctx.state.library;
  dupeShown = DUPE_PAGE;
  const tab = tabOf(ctx.param);
  return `<h1>Внимание</h1><p class="sub">То, что сервис не может решить сам</p>
    <div id="att-tabs">${tabs(tab, data)}</div><div id="att-body">${body(tab, data)}</div>`;
}

function redraw(root, force = false) {
  // фоновое обновление не обрывает играющий плеер; действия пользователя — перерисовывают
  if (!force && [...root.querySelectorAll("audio")].some((a) => !a.paused)) return;
  const tab = tabOf((location.hash.split("/")[2] || ""));
  root.querySelector("#att-tabs").innerHTML = tabs(tab, data);
  root.querySelector("#att-body").innerHTML = body(tab, data);
}

export function mount(root) {
  mountMissing(root, {
    getTracks: () => (data ? visibleMissing(data) : []),
    bulkUrl: () => (plFilter ? `/api/v2/playlists/${plFilter}/fetch-missing` : "/api/v2/attention/fetch-missing"),
    rerenderBulk: () => {
      const n = visibleMissing(data).filter((t) => t.spotify_url).length;
      const el = root.querySelector("#att-bulk");
      if (el) el.innerHTML = n ? bulkBar(n) : "";
    },
  });
  on(root, "change", "#att-pl", (e, sel) => { plFilter = sel.value; redraw(root, true); });
  on(root, "click", "[data-pl-filter]", (e, b) => { plFilter = b.dataset.plFilter; redraw(root, true); window.scrollTo(0, 0); });
  on(root, "click", "[data-dlevel]", (e, b) => { dupeLevel = b.dataset.dlevel; dupeShown = DUPE_PAGE; redraw(root, true); });
  on(root, "click", "[data-dmore]", () => { dupeShown += DUPE_PAGE; redraw(root, true); });
  on(root, "click", "[data-listen]", async (e, b) => {
    const id = b.dataset.listen;
    const slot = root.querySelector(`[data-player="${id}"]`);
    root.querySelectorAll("audio").forEach((a) => a.pause());        // одна запись за раз
    let audio = slot.querySelector("audio");
    if (!audio) {
      try {
        const r = await api("POST", `/api/v2/tracks/${id}/listen`);
        slot.innerHTML = `<audio class="player" controls preload="none" src="${esc(r.url)}"></audio>`;
        audio = slot.querySelector("audio");
        audio.addEventListener("play", () => root.querySelectorAll("audio").forEach((a) => { if (a !== audio) a.pause(); }));
      } catch (ex) { toast(ex.message); return; }
    }
    audio.play().catch(() => toast("Не удалось воспроизвести — ссылка могла устареть"));
  });
  on(root, "click", "[data-decide]", async (e, b) => {
    const card = b.closest("[data-dupe]");
    const id = Number(card.dataset.dupe);
    const decision = b.dataset.decide;
    try {
      await api("POST", `/api/v2/dupes/${id}`, { decision });
      root.querySelectorAll("audio").forEach((a) => a.pause());
      data.dupes = data.dupes.filter((p) => p.id !== id);
      if (decision === "same") data.dupes_pending_merge += 1;
      toast(decision === "same" ? "Отмечено: склеится на следующем этапе" : "Отмечено: разные треки");
      redraw(root, true);
      refreshLibrary();                  // счётчик «Внимания» в меню
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-bulk-same]", async () => {
    const n = data.dupes.filter((p) => p.level <= 3).length;
    if (!confirm(`Отметить ${n} ${plural(n, "уверенную пару", "уверенные пары", "уверенных пар")} как «один трек»? Файлы сейчас не трогаются — склейка будет на следующем этапе.`)) return;
    try {
      const r = await api("POST", "/api/v2/dupes/bulk", { levels: [1, 2, 3], decision: "same" });
      data.dupes = data.dupes.filter((p) => p.level > 3);
      data.dupes_pending_merge += r.updated;
      toast(`Отмечено ${fmtNum(r.updated)}`);
      root.querySelectorAll("audio").forEach((a) => a.pause());
      redraw(root, true);
      refreshLibrary();
    } catch (ex) { toast(ex.message); }
  });
  on(root, "click", "[data-remove]", async (e, b) => {
    if (!confirm(`Убрать «${b.dataset.name}» из плейлистов? Скачанные файлы останутся.`)) return;
    try {
      await api("DELETE", "/api/playlists", { url: b.dataset.remove });
      toast("Убран");
      pokeSoon();
    } catch (ex) { toast(ex.message); }
  });
  return () => root.querySelectorAll("audio").forEach((a) => a.pause());
}

let refetching = false;
async function refetch(root) {
  if (refetching) return;
  refetching = true;
  try {
    data = await api("GET", "/api/v2/attention");
    setCaps(data.caps);
    redraw(root);
  } catch (e) { /* попробуем со следующим снимком */ }
  refetching = false;
}

export function onState(root, st) {
  if (data) updateMissingJobs(root, st, () => visibleMissing(data));
  if (st.library && st.library !== lastLibrary) {
    lastLibrary = st.library;
    refetch(root);
  }
}
