// Раздел, которого ещё нет (design/plan.md): честно говорим, когда появится.
import { esc, icon } from "../ui.js";

const LATER = {
  search: "Поиск по библиотеке и площадкам — этап 5 плана. Пока ищите в «Библиотеке».",
  discover: "«Открытия» — отложенный этап плана: подборки площадок и ListenBrainz.",
};

export function render(ctx) {
  const nav = ctx.nav || { title: "Раздел", icon: "info-circle" };
  return `<h1>${esc(nav.title)}</h1>
    <div class="card stub" style="margin-top:18px">${icon(nav.icon, "xl")}
      <p style="margin:12px 0 4px;color:var(--text)">Раздел появится позже</p>
      <p class="small" style="margin:0 0 16px">${esc(LATER[ctx.view] || "Он в плане следующих этапов.")}</p>
    </div>`;
}
