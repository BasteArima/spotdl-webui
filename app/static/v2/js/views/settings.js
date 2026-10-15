// Настройки. В 2.1 — только оформление и вход; подстраницы со всеми полями
// (design/plan.md, задача 2.6) пока ведут в прежний интерфейс.
import { getTheme, setTheme } from "../main.js";
import { signOut } from "../api.js";
import { icon, on, toast } from "../ui.js";

const row = (ic, title, sub, right) => `<div class="li">${icon(ic, "muted")}<div class="grow"><div>${title}</div>${
  sub ? `<div class="tiny muted">${sub}</div>` : ""}</div>${right}</div>`;

export function render() {
  const t = getTheme();
  const seg = [["auto", "Авто"], ["light", "Светлая"], ["dark", "Тёмная"]]
    .map(([k, l]) => `<button data-theme-set="${k}" class="${t === k ? "on" : ""}">${l}</button>`).join("");
  return `<h1>Настройки</h1><p class="sub">Остальные настройки переезжают — пока они в прежнем интерфейсе</p>
    <div class="section-label">Внешний вид</div>
    <div class="card list">${row("sun-moon", "Тема", "", `<div class="seg">${seg}</div>`)}</div>
    <div class="section-label">Сервис</div>
    <div class="card list">
      ${row("adjustments", "Все настройки", "источники, синк, загрузка, Navidrome, доступ", `<a class="btn sm" href="/">${icon("external-link", "sm")}Прежний интерфейс</a>`)}
      ${row("logout", "Выйти", "на этом устройстве", `<button class="btn sm" data-logout>Выйти</button>`)}
    </div>`;
}

export function mount(root) {
  on(root, "click", "[data-theme-set]", (e, b) => {
    setTheme(b.dataset.themeSet);
    root.querySelectorAll("[data-theme-set]").forEach((x) => x.classList.toggle("on", x === b));
  });
  on(root, "click", "[data-logout]", () => { signOut(); toast("Вы вышли"); });
}
