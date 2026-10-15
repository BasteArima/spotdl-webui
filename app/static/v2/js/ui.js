// Общие кирпичики нового интерфейса: иконки, экранирование, тосты, обложки,
// значки источников, форматирование. Разметка — строками (как в прототипе),
// события — делегированием (on(root, "click", "[data-x]", fn)).

export const ICONS = "/static/v2/icons.svg";

export function icon(name, cls = "") {
  return `<svg class="i ${cls}" aria-hidden="true"><use href="${ICONS}#${name}"></use></svg>`;
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Шаблон с экранированием: html`<b>${name}</b>` экранирует name; raw(x) — вставить как есть.
export const raw = (s) => ({ __raw: String(s ?? "") });
export function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    if (Array.isArray(v)) out += v.map((x) => (x && x.__raw !== undefined ? x.__raw : esc(x))).join("");
    else out += v && v.__raw !== undefined ? v.__raw : esc(v);
    out += strings[i + 1];
  });
  return raw(out);
}

export function on(root, event, selector, fn) {
  root.addEventListener(event, (e) => {
    const t = e.target.closest(selector);
    if (t && root.contains(t)) fn(e, t);
  });
}

let toastTimer = null;
export function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

export function fmtNum(n) {
  return (n ?? 0).toLocaleString("ru-RU");
}

export function fmtAgo(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return `${Math.round(s / 86400)} ${plural(Math.round(s / 86400), "день", "дня", "дней")} назад`;
}

export function fmtIn(sec) {
  if (sec == null) return "";
  if (sec < 90) return "через минуту";
  if (sec < 3600) return `через ${Math.round(sec / 60)} мин`;
  if (sec < 48 * 3600) return `через ${Math.round(sec / 3600)} ч`;
  return `через ${Math.round(sec / 86400)} дн`;
}

export function fmtLeft(sec) {
  if (sec == null) return "";
  if (sec < 90) return "меньше минуты";
  if (sec < 3600) return `ещё ~${Math.round(sec / 60)} мин`;
  if (sec < 48 * 3600) return `ещё ~${Math.round(sec / 3600)} ч`;
  return `ещё ~${Math.round(sec / 86400)} дн`;
}

const COLORS = ["#5e5ce6", "#bf5af2", "#ff375f", "#ff9f0a", "#30b0c7", "#34c759", "#0a84ff", "#ac8e68", "#64d2ff", "#ff6961"];
export function coverColor(s) {
  let h = 0;
  for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}
export function cover(name, cls = "") {
  const letter = String(name || "").replace(/[^\p{L}\p{N}]/gu, "").slice(0, 1).toUpperCase() || "♪";
  return `<div class="cover ${cls}" style="background:${coverColor(name)}">${esc(letter)}</div>`;
}

export const SRC = {
  spotify: { name: "Spotify", icon: "brand-spotify" },
  ytm: { name: "YouTube Music", icon: "brand-youtube" },
  youtube: { name: "YouTube", icon: "brand-youtube" },
  deezer: { name: "Deezer", icon: "music" },
  manual: { name: "Вручную", icon: "upload" },
};
export function srcIcon(t) {
  const s = SRC[t] || SRC.manual;
  return `<span class="src ${t === "youtube" ? "ytm" : t}" title="${esc(s.name)}">${icon(s.icon, "sm")}</span>`;
}
export const srcIcons = (arr) => `<span class="srcs">${(arr || []).map(srcIcon).join("")}</span>`;

export const chip = (text, cls = "") => `<span class="chip ${cls}">${esc(text)}</span>`;

export function bar(pct) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  return `<div class="bar"><i style="width:${p}%"></i></div>`;
}
