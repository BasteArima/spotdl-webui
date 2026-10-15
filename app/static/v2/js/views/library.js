// Библиотека: что лежит в /music — треки, альбомы, исполнители; поиск,
// фильтр по источнику, подгрузка порциями (тысячи треков).
import { api } from "../api.js";
import { cover, esc, fmtNum, icon, on, plural, srcIcons } from "../ui.js";

const PAGE = 100;
let st = { view: "tracks", q: "", platform: "", drill: null, items: [], total: 0, overview: null };
let lastLibrary = null;
let searchTimer = null;

const fmtDur = (s) => (s ? `${Math.floor(Math.round(s) / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}` : "");
function fmtSize(b) {
  if (!b) return "0 МБ";
  return b >= 1e9 ? `${(b / 1e9).toFixed(1).replace(".", ",")} ГБ` : `${Math.round(b / 1e6)} МБ`;
}

function stats(o) {
  if (!o) return "";
  const card = (n, l, href) => `<${href ? `a href="${href}"` : "div"} class="card pad stat" style="color:inherit;display:block">
    <div class="n">${n}</div><div class="small muted">${l}</div></${href ? "a" : "div"}>`;
  return `<div class="grid lib-stats">
    ${card(fmtNum(o.tracks), plural(o.tracks, "трек", "трека", "треков"))}
    ${card(fmtNum(o.hi_quality), `от ${o.min_bitrate} кбит/с`)}
    ${card(fmtNum(o.multi), "из нескольких источников")}
    ${card(fmtNum(o.upgradeable), "можно улучшить до 320k", "#/quality")}
  </div>`;
}

function row(item) {
  if (st.view === "albums") {
    return `<button class="li click" data-drill-album="${esc(item.album)}" data-drill-aa="${esc(item.album_artist)}" style="width:100%;border:0;background:none;text-align:left;color:inherit">
      ${cover(item.album || "?")}<div class="grow"><div class="ellip">${esc(item.album || "Без альбома")}</div>
      <div class="tiny muted ellip">${esc([item.album_artist, item.year, `${item.n} ${plural(item.n, "трек", "трека", "треков")}`].filter(Boolean).join(" · "))}</div></div>
      <span class="tiny muted hide-m">${item.bitrate ? `~${item.bitrate}k` : ""}</span>${icon("chevron-right", "muted")}</button>`;
  }
  if (st.view === "artists") {
    return `<button class="li click" data-drill-artist="${esc(item.artist)}" style="width:100%;border:0;background:none;text-align:left;color:inherit">
      <div class="cover" style="border-radius:50%;background:var(--fill2);color:var(--text)">${esc((item.artist || "?").slice(0, 1).toUpperCase())}</div>
      <div class="grow"><div class="ellip">${esc(item.artist || "Без исполнителя")}</div>
      <div class="tiny muted">${item.n} ${plural(item.n, "трек", "трека", "треков")} · ${item.albums} ${plural(item.albums, "альбом", "альбома", "альбомов")}</div></div>
      ${icon("chevron-right", "muted")}</button>`;
  }
  return `<div class="li">${cover(item.album || item.title)}<div class="grow"><div class="ellip">${esc(item.title)}</div>
    <div class="tiny muted ellip">${esc([item.artist, item.album].filter(Boolean).join(" · "))}</div></div>
    ${srcIcons(item.platforms)}<span class="tiny muted hide-m">${esc(item.codec || "")}${item.bitrate ? " " + item.bitrate + "k" : ""}</span>
    <span class="tiny muted hide-m" style="width:36px;text-align:right">${fmtDur(item.duration)}</span></div>`;
}

function listHtml() {
  if (!st.items.length) {
    return `<div class="li"><div class="grow muted">${st.q || st.platform ? "Ничего не нашлось" : "Библиотека пока пуста"}</div></div>`;
  }
  return st.items.map(row).join("") + (st.items.length < st.total
    ? `<div class="li" style="justify-content:center"><button class="btn sm" data-more>Показать ещё (${fmtNum(st.total - st.items.length)})</button></div>` : "");
}

function controls() {
  const views = [["tracks", "Треки"], ["albums", "Альбомы"], ["artists", "Исполнители"]];
  const plats = [["", "Все"], ["spotify", "Spotify"], ["ytm", "YouTube"], ["manual", "Вручную"]];
  return `<div class="row" style="margin:22px 0 10px;flex-wrap:wrap;gap:10px">
    <div class="seg">${views.map(([k, l]) => `<button data-view="${k}" class="${st.view === k ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="search" style="height:34px;flex:1;min-width:180px;box-shadow:none">${icon("filter", "muted")}
      <input id="lib-q" placeholder="Название, исполнитель, альбом" value="${esc(st.q)}">${st.q ? `<button class="btn icon sm" data-clear aria-label="Очистить">${icon("x", "sm")}</button>` : ""}</div>
    <div class="seg">${plats.map(([k, l]) => `<button data-platform="${k}" class="${st.platform === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    ${st.drill ? `<div style="margin:0 0 10px"><button class="chip info" data-undrill style="border:0;cursor:pointer">${esc(st.drill.label)} ${icon("x", "sm")}</button></div>` : ""}
    <div class="tiny muted" style="margin:0 4px 8px" id="lib-count">${countText()}</div>`;
}

function countText() {
  const unit = st.view === "albums" ? ["альбом", "альбома", "альбомов"] : st.view === "artists" ? ["исполнитель", "исполнителя", "исполнителей"] : ["трек", "трека", "треков"];
  return `${fmtNum(st.total)} ${plural(st.total, ...unit)}`;
}

async function load(append = false) {
  const p = new URLSearchParams({ view: st.view, q: st.q, platform: st.platform, offset: append ? st.items.length : 0, limit: PAGE });
  if (st.drill) Object.entries(st.drill.params).forEach(([k, v]) => p.set(k, v));
  const d = await api("GET", `/api/v2/library?${p}`);
  st.items = append ? st.items.concat(d.items) : d.items;
  st.total = d.total;
  if (d.overview) st.overview = d.overview;
}

export async function render(ctx) {
  lastLibrary = ctx.state.library;
  await load();
  const o = st.overview;
  return `<h1>Библиотека</h1>
    <p class="sub">${o ? `${fmtNum(o.tracks)} ${plural(o.tracks, "трек", "трека", "треков")} · ${fmtNum(o.artists)} ${plural(o.artists, "исполнитель", "исполнителя", "исполнителей")} · ${fmtNum(o.albums)} ${plural(o.albums, "альбом", "альбома", "альбомов")} · ${fmtSize(o.size)}` : ""}</p>
    <div id="lib-stats">${stats(o)}</div><div id="lib-controls">${controls()}</div>
    <div class="card list" id="lib-list">${listHtml()}</div>`;
}

async function reload(root, keepFocus) {
  await load();
  root.querySelector("#lib-list").innerHTML = listHtml();
  root.querySelector("#lib-count").innerHTML = countText();
  if (!keepFocus) {
    root.querySelector("#lib-controls").innerHTML = controls();
  }
}

export function mount(root) {
  on(root, "click", "[data-view]", async (e, b) => {
    st.view = b.dataset.view;
    st.drill = null;                 // переход из альбома/исполнителя — только для «Треков»
    await reload(root);
  });
  on(root, "click", "[data-platform]", async (e, b) => {
    st.platform = b.dataset.platform;
    await reload(root);
  });
  on(root, "input", "#lib-q", (e, inp) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => { st.q = inp.value; await reload(root, true); }, 250);
  });
  on(root, "click", "[data-clear]", async () => {
    st.q = "";
    await reload(root);
  });
  on(root, "click", "[data-drill-album], [data-drill-artist]", async (e, b) => {
    // альбом / исполнитель → его треки (точный фильтр, не текстовый поиск)
    st.view = "tracks";
    st.q = "";
    st.drill = b.dataset.drillArtist !== undefined
      ? { label: `Исполнитель: ${b.dataset.drillArtist}`, params: { artist: b.dataset.drillArtist } }
      : { label: `Альбом: ${b.dataset.drillAlbum}`, params: { album: b.dataset.drillAlbum, album_artist: b.dataset.drillAa } };
    await reload(root);
    window.scrollTo(0, 0);
  });
  on(root, "click", "[data-undrill]", async () => {
    st.drill = null;
    await reload(root);
  });
  on(root, "click", "[data-more]", async (e, b) => {
    b.disabled = true;
    await load(true);
    root.querySelector("#lib-list").innerHTML = listHtml();
  });
  return () => clearTimeout(searchTimer);
}

export function onState(root, s) {
  if (s.library && s.library !== lastLibrary) {
    lastLibrary = s.library;
    (async () => {
      try {
        await load();
        root.querySelector("#lib-stats").innerHTML = stats(st.overview);
        root.querySelector("#lib-list").innerHTML = listHtml();
        root.querySelector("#lib-count").innerHTML = countText();
      } catch (e) { /* следующий снимок */ }
    })();
  }
}
