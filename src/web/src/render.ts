// Rendering: sidebar (always) + the routed detail panel. The view is chosen by
// the current Route (URL), not by stored selection state.

import { $, esc } from "./dom";
import { store } from "./state";
import { currentRoute, instancePath, type Route } from "./router";
import { instanceView } from "./pages/instance";
import { usageView } from "./pages/usage";
import { welcome } from "./pages/welcome";

// The instance whose row is highlighted / whose console the term targets.
export function activeInstanceName(): string | null {
  const r = currentRoute();
  if (r.page === "instance") return r.name;
  return null;
}

function listItem(r: { name: string; running: boolean }): string {
  const sel = r.name === activeInstanceName();
  const dot = r.running ? "bg-emerald-500" : "bg-neutral-300 dark:bg-neutral-600";
  const b = store.busy[r.name];
  return `<a href="${instancePath(r.name)}" data-link class="w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${sel ? "bg-white dark:bg-neutral-800 shadow-sm" : "hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${dot} shrink-0"></span>
     <span class="truncate ${sel ? "font-medium text-neutral-900 dark:text-neutral-50" : "text-neutral-700 dark:text-neutral-300"}">${esc(r.name)}</span>
     ${b ? `<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>` : ""}</a>`;
}

export function renderSidebar(): void {
  const items = store.data.instances;
  $("list").innerHTML = items.map(listItem).join("");
  const running = items.filter((i) => i.running).length;
  $("runcount").textContent = running + "/" + items.length;
  $("footstat").textContent = items.length
    ? running + " of " + items.length + " running" : "no instances yet";
}

// Detail re-render guard: don't clobber an open input/modal or unchanged DOM.
let detailSig = "";
function detailSignature(route: Route): string {
  if (route.page === "instance") {
    const r = store.data.instances.find((i) => i.name === route.name) || null;
    if (!r) return "instance:none:" + route.name;
    return JSON.stringify(["instance", r.name, r.running, r.server, r.focus,
      r.project, !!r.pending, store.busy[r.name] || "", store.data.plugins.length]);
  }
  if (route.page === "usage") return "usage:" + (store.usage ? "loaded" : "pending");
  return route.page;
}

function userIsInteracting(): boolean {
  const a = document.activeElement as HTMLElement | null;
  if (a && (a.tagName === "INPUT" || a.tagName === "SELECT" || a.tagName === "TEXTAREA")) return true;
  if (!$("modal").classList.contains("hidden")) return true;
  return false;
}

function viewForRoute(route: Route): string {
  switch (route.page) {
    case "usage": return usageView();
    case "instance": {
      const r = store.data.instances.find((i) => i.name === route.name) || null;
      return instanceView(r);
    }
    case "home": {
      // Auto-show the first instance if any exist; else the welcome.
      const first = store.data.instances[0];
      return first ? instanceView(first) : welcome();
    }
    default: return welcome();
  }
}

export function renderDetail(force: boolean): void {
  const route = currentRoute();
  const sig = detailSignature(route);
  if (!force && sig === detailSig) return;
  if (!force && userIsInteracting()) return;
  detailSig = sig;
  $("detail").innerHTML = viewForRoute(route);
}

// Full render (on navigation / explicit action).
export function render(): void { renderSidebar(); renderDetail(true); }
