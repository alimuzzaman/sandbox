// Custom styled <select> replacement. opts: [{v,label}]. Searchable when >8.
// The pick callback is registered by id (no eval, unlike the old JS).

import { esc } from "../dom";

export interface CselOpt { v: string; label: string }

let seq = 0;
const callbacks: Record<string, (v: string) => void> = {};

export function nextCselId(): string { return "mcsel" + (++seq); }

export function csel(
  id: string,
  opts: CselOpt[],
  value: string,
  onPick: (v: string) => void,
  disabled?: boolean,
  fullWidth?: boolean,
): string {
  const cur = opts.find((o) => o.v === value);
  const label = cur ? cur.label : (opts[0] ? opts[0].label : "");
  callbacks[id] = onPick;
  const dis = disabled ? "opacity-50 pointer-events-none" : "";
  const wrapW = fullWidth ? "block w-full" : "inline-block";
  const ctrlW = fullWidth ? "w-full" : "w-48";
  return `<div class="relative ${wrapW}" data-csel="${id}">
    <button type="button" onclick="sb.cselToggle('${id}')"
      class="${ctrlW} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${dis}">
      <span class="truncate flex-1" data-csel-label>${esc(label)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${ctrlW} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${opts.length > 8 ? `<input data-csel-search oninput="sb.cselFilter('${id}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">` : ""}
      <div data-csel-list>
        ${opts.map((o) => `<button type="button" data-v="${esc(o.v)}" data-search="${esc(o.label.toLowerCase())}"
          onclick="sb.cselPick('${id}','${esc(o.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${o.v === value ? "text-accent dark:text-blue-400 font-medium" : "text-neutral-700 dark:text-neutral-300"}">
          ${esc(o.label)}</button>`).join("")}
      </div>
    </div></div>`;
}

export function cselToggle(id: string): void {
  document.querySelectorAll("[data-csel-pop]").forEach((p) => {
    const el = p.closest("[data-csel]") as HTMLElement | null;
    if (el && el.dataset.csel !== id) p.classList.add("hidden");
  });
  const pop = document.querySelector(`[data-csel="${id}"] [data-csel-pop]`);
  if (!pop) return;
  pop.classList.toggle("hidden");
  if (!pop.classList.contains("hidden")) {
    const s = pop.querySelector("[data-csel-search]") as HTMLInputElement | null;
    if (s) { s.value = ""; cselFilter(id); s.focus(); }
  }
}

export function cselPick(id: string, v: string): void {
  document.querySelector(`[data-csel="${id}"] [data-csel-pop]`)?.classList.add("hidden");
  const lbl = document.querySelector(`[data-csel="${id}"] [data-csel-label]`);
  const btn = document.querySelector(`[data-csel="${id}"] [data-v="${CSS.escape(v)}"]`);
  if (lbl && btn) lbl.textContent = btn.textContent!.trim();
  callbacks[id]?.(v);
}

export function cselFilter(id: string): void {
  const search = document.querySelector(
    `[data-csel="${id}"] [data-csel-search]`) as HTMLInputElement | null;
  const q = (search?.value || "").toLowerCase();
  document.querySelectorAll(`[data-csel="${id}"] [data-csel-list] button`)
    .forEach((b) => {
      const el = b as HTMLElement;
      el.style.display = (el.dataset.search || "").includes(q) ? "" : "none";
    });
}

// Close any open popup when clicking outside a csel.
export function initCselOutsideClose(): void {
  document.addEventListener("click", (e) => {
    if (!(e.target as HTMLElement)?.closest?.("[data-csel]"))
      document.querySelectorAll("[data-csel-pop]").forEach((p) => p.classList.add("hidden"));
  });
}
