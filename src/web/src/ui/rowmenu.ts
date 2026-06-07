// Per-row "⋯ more actions" popover for the sidebar instance list. One menu is
// open at a time, anchored to the row's kebab button. Mirrors csel's pattern:
// a toggle + a single document-level outside-click closer (initRowMenuClose).
//
// The menu markup is rendered inline by render.ts (so it re-renders with the
// row); this module only owns open/close + the toggle handler exposed on sb.

import { esc } from "../dom";

export interface RowMenuItem {
  label: string;
  js: string;            // sb.* handler call, e.g. "sb.act('foo','stop')"
  icon?: string;         // optional inline SVG
  danger?: boolean;      // red styling (Delete)
  disabled?: boolean;
}

// Build the ⋯ trigger + hidden popover. `id` must be unique per row.
export function rowMenu(id: string, items: RowMenuItem[]): string {
  const rows = items.map((it) => {
    const dis = it.disabled ? "opacity-40 pointer-events-none" : "";
    const color = it.danger
      ? "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40"
      : "text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";
    // Each item closes the menu, then prevents the row's nav, then runs.
    return `<button type="button" ${it.disabled ? "disabled" : ""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${it.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${color} ${dis}">
      ${it.icon ? `<span class="w-3.5 h-3.5 grid place-items-center shrink-0 opacity-70">${it.icon}</span>` : ""}
      <span class="flex-1">${esc(it.label)}</span></button>`;
  }).join("");

  return `<span class="relative shrink-0" data-rowmenu="${id}">
    <button type="button" title="More actions" aria-label="More actions"
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuToggle('${id}')"
      class="w-6 h-6 grid place-items-center rounded text-neutral-500 dark:text-neutral-400
      hover:bg-neutral-200 dark:hover:bg-neutral-700 hover:text-neutral-900 dark:hover:text-neutral-100">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
    </button>
    <div data-rowmenu-pop class="hidden absolute right-0 z-[60] mt-1 min-w-[10rem] py-1
      rounded-lg border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl">
      ${rows}</div></span>`;
}

export function rowMenuToggle(id: string): void {
  let wasOpen = false;
  document.querySelectorAll("[data-rowmenu-pop]").forEach((p) => {
    const el = p.closest("[data-rowmenu]") as HTMLElement | null;
    const open = !p.classList.contains("hidden");
    if (el && el.dataset.rowmenu === id) wasOpen = open;
    p.classList.add("hidden");               // close all first
  });
  if (wasOpen) return;                        // toggling the open one → just close
  document.querySelector(`[data-rowmenu="${id}"] [data-rowmenu-pop]`)
    ?.classList.remove("hidden");
}

export function rowMenuClose(): void {
  document.querySelectorAll("[data-rowmenu-pop]")
    .forEach((p) => p.classList.add("hidden"));
}

// Close on any click outside an open menu (registered once at boot).
export function initRowMenuClose(): void {
  document.addEventListener("click", (e) => {
    if (!(e.target as HTMLElement)?.closest?.("[data-rowmenu]")) rowMenuClose();
  });
}
