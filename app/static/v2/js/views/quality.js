// «Качество 320k»: фоновая замена треков ниже порога версией прямо со Spotify
// (librespot), с паузами и лимитом в сутки — как прежняя вкладка «Апгрейд 320k».
import { api } from "../api.js";
import { pokeSoon } from "../store.js";
import { bar, cover, esc, fmtAgo, fmtLeft, fmtNum, icon, on, plural, toast } from "../ui.js";

let data = null;
let lastUpgrade = "";
let timer = null;

const STATE = {
  idle: ["Не запущено", ""], running: ["Идёт", "ok"], waiting: ["Ждёт новых суток", "info"],
  stopping: ["Останавливается", ""], stopped: ["Остановлено", ""],
};
const RESULT = {
  upgraded: ["улучшен до 320k", "var(--accent)"],
  good: ["уже хорошее качество — пропущен", "var(--text2)"],
  failed: ["не вышло", "var(--orange)"],
};

function statusCard(d) {
  const s = d.status, o = d.overview;
  const [label, cls] = STATE[s.state] || [s.state, ""];
  const active = ["running", "waiting", "stopping"].includes(s.state);
  const processed = s.done + s.failed + s.skipped;
  const pct = s.total ? (processed / s.total) * 100 : 0;
  const sub = active
    ? `${fmtNum(processed)} из ${fmtNum(s.total)} проверено${s.eta_seconds != null ? " · " + fmtLeft(s.eta_seconds) : ""}`
    : `можно улучшить ${fmtNum(o.upgradeable)} ${plural(o.upgradeable, "трек", "трека", "треков")} · уже обработано ${fmtNum(s.upgraded_total)}`;
  let btn;
  if (!d.configured) btn = `<a class="btn" href="#/settings">${icon("settings")}Настроить Spotify напрямую</a>`;
  else if (s.state === "running" || s.state === "waiting") btn = `<button class="btn" data-stop>${icon("player-pause")}Остановить</button>`;
  else if (s.state === "stopping") btn = `<button class="btn" disabled>${icon("loader-2")}Останавливается…</button>`;
  else btn = `<button class="btn primary" data-start>${icon("player-play")}${s.state === "stopped" ? "Продолжить" : "Запустить"}</button>`;
  return `<div class="card pad">
    <div class="row" style="flex-wrap:wrap;gap:10px"><div class="grow">
      <div class="row" style="gap:8px;flex-wrap:wrap"><b style="font-weight:600;font-size:17px">${label}</b>${
        s.current ? `<span class="chip ${cls}">${esc(s.current)}</span>` : ""}</div>
      <div class="small muted" style="margin-top:2px">${esc(sub)}</div></div>${btn}</div>
    ${active ? `<div style="margin:14px 0 12px">${bar(pct)}</div>` : `<div style="height:12px"></div>`}
    <div class="grid lib-stats">
      ${[[s.done, "обработано за запуск"], [s.skipped, "отмечены раньше"], [s.failed, "не вышло"], [o.below_threshold, `в библиотеке ниже ${o.min_bitrate} кбит/с`]]
        .map(([n, l]) => `<div class="stat"><div class="n" style="font-size:22px">${fmtNum(n)}</div><div class="tiny muted">${l}</div></div>`).join("")}
    </div></div>`;
}

const setRow = (ic, title, sub, right) => `<div class="li">${icon(ic, "muted")}<div class="grow"><div>${title}</div>${
  sub ? `<div class="tiny muted">${sub}</div>` : ""}</div>${right}</div>`;

function settingsCard(d) {
  const c = d.settings;
  return `<div class="section-label">Как идёт</div><div class="card list">
    ${setRow("shield-check", "Безопасный режим", `со скоростью прослушивания и паузой ${c.bulk_wait_min}–${c.bulk_wait_max} с между треками`,
      `<button class="toggle ${c.safe_mode ? "on" : ""}" data-set="safe_mode" aria-label="Безопасный режим"></button>`)}
    ${setRow("calendar", "Лимит в сутки", "0 — без лимита; меньше треков в день — меньше риск для аккаунта",
      `<input class="field" style="width:80px" type="number" min="0" data-num="upgrade_per_day" value="${c.upgrade_per_day}">`)}
    ${setRow("gauge", "Не трогать треки от", "файлы с битрейтом не ниже этого пропускаются",
      `<span class="row" style="gap:6px"><input class="field" style="width:80px" type="number" min="0" data-num="upgrade_min_bitrate" value="${c.upgrade_min_bitrate}"><span class="muted small">кбит/с</span></span>`)}
  </div>`;
}

function recentCard(d) {
  const r = d.status.recent || [];
  if (!r.length) return "";
  return `<div class="section-label">Последние</div><div class="card list">${r.map((x) => {
    const [txt, color] = RESULT[x.result] || [x.result, "var(--text2)"];
    return `<div class="li">${cover(x.name)}<div class="grow"><div class="ellip">${esc(x.name)}</div>
      <div class="tiny muted">${esc(fmtAgo(x.ts))}</div></div><span class="tiny" style="color:${color}" title="${esc(x.detail || "")}">${txt}</span></div>`;
  }).join("")}</div>`;
}

function page(d) {
  return `<div id="q-status">${statusCard(d)}</div><div id="q-settings">${settingsCard(d)}</div><div id="q-recent">${recentCard(d)}</div>
    <p class="tiny muted" style="margin-top:12px">Риск блокировки аккаунта Spotify не нулевой — паузы и лимит его снижают, но не убирают. Улучшенные треки помнятся между перезапусками.</p>`;
}

export async function render() {
  data = await api("GET", "/api/v2/quality");
  return `<h1>Улучшение до 320k</h1><p class="sub">Заменяет треки ниже порога версией прямо со Spotify. Идёт медленно — со скоростью прослушивания и паузами, так безопаснее для аккаунта.</p>
    <div id="q-page">${page(data)}</div>`;
}

async function refresh(root) {
  try {
    data = await api("GET", "/api/v2/quality");
    root.querySelector("#q-status").innerHTML = statusCard(data);
    root.querySelector("#q-recent").innerHTML = recentCard(data);
  } catch (e) { /* в следующий раз */ }
}

export function mount(root) {
  on(root, "click", "[data-start]", async () => {
    try { await api("POST", "/api/upgrade/start"); toast("Улучшение запущено"); }
    catch (ex) { toast(ex.message); }
    pokeSoon();
    refresh(root);
  });
  on(root, "click", "[data-stop]", async () => {
    try { await api("POST", "/api/upgrade/stop"); toast("Останавливается — текущий трек дописывается"); }
    catch (ex) { toast(ex.message); }
    pokeSoon();
    refresh(root);
  });
  on(root, "click", "[data-set]", async (e, b) => {
    const val = !b.classList.contains("on");
    try {
      await api("POST", "/api/settings", { [b.dataset.set]: val });
      b.classList.toggle("on", val);
      toast(val ? "Безопасный режим включён" : "Безопасный режим выключен");
    } catch (ex) { toast(ex.message); }
  });
  on(root, "change", "[data-num]", async (e, inp) => {
    const v = parseInt(inp.value, 10);
    if (Number.isNaN(v) || v < 0) { toast("Нужно целое число от 0"); return; }
    try { await api("POST", "/api/settings", { [inp.dataset.num]: v }); toast("Сохранено"); refresh(root); }
    catch (ex) { toast(ex.message); }
  });
  // пока апгрейд идёт — обновлять «Последние» и счётчики (в снимке очереди их нет)
  timer = setInterval(() => {
    if (data && ["running", "waiting", "stopping"].includes(data.status.state)) refresh(root);
  }, 5000);
  return () => clearInterval(timer);
}

export function onState(root, s) {
  const u = s.jobs && s.jobs.upgrade;
  const key = u ? `${u.state}:${u.done}:${u.failed}:${u.skipped}` : "";
  if (key && key !== lastUpgrade) {
    lastUpgrade = key;
    refresh(root);
  }
}
