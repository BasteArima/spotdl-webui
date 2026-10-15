// Раздел, который ещё переезжает в новый интерфейс (design/plan.md, этап 2):
// честно говорим об этом и ведём в прежний интерфейс.
import { esc, icon } from "../ui.js";

const LEGACY = {
  home: "/", search: "/", playlists: "/", library: "/", quality: "/", attention: "/",
  discover: null, activity: "/", settings: "/",
};

export function render(ctx) {
  const nav = ctx.nav || { title: "Раздел", icon: "info-circle" };
  const legacy = LEGACY[ctx.view];
  return `<h1>${esc(nav.title)}</h1>
    <div class="card stub" style="margin-top:18px">${icon(nav.icon, "xl")}
      <p style="margin:12px 0 4px;color:var(--text)">Раздел переезжает в новый интерфейс</p>
      <p class="small" style="margin:0 0 16px">${ctx.view === "discover"
        ? "«Открытия» — отложенный этап плана: подборки площадок и ListenBrainz."
        : "Пока он работает в прежнем интерфейсе."}</p>
      ${legacy ? `<a class="btn tinted" href="${legacy}">${icon("external-link")}Открыть прежний интерфейс</a>` : ""}
    </div>`;
}
