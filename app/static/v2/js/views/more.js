// «Ещё» — разделы, не поместившиеся во вкладки на телефоне.
import { icon } from "../ui.js";

const ITEMS = [
  ["library", "Библиотека", "vinyl"],
  ["quality", "Качество 320k", "arrow-big-up-lines"],
  ["discover", "Открытия", "sparkles"],
  ["activity", "Активность", "history"],
  ["settings", "Настройки", "settings"],
];

export function render() {
  return `<h1>Ещё</h1><div class="card list" style="margin-top:14px">${ITEMS.map(([v, t, ic]) =>
    `<a class="li click" href="#/${v}" style="color:inherit">${icon(ic)}<div class="grow">${t}</div>${icon("chevron-right", "muted")}</a>`).join("")}</div>`;
}
