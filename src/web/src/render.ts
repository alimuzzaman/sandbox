// Rendering: sidebar (always) + the routed detail panel. The view is chosen by
// the current Route (URL), not by stored selection state.

import { $, esc } from "./dom";
import { store } from "./state";
import { currentRoute, instancePath, type Route } from "./router";
import { instanceView } from "./pages/instance";
import { usageView } from "./pages/usage";
import { hostsView, sidebarHostSelector } from "./pages/hosts";
import { createView } from "./pages/create";
import { remoteView } from "./pages/remote";
import { rowMenu, type RowMenuItem } from "./ui/rowmenu";
import type { Instance } from "./types";

// The instance whose row is highlighted / whose console the term targets.
export function activeInstanceName(): string | null {
  const r = currentRoute();
  if (r.page === "instance") return r.name;
  return null;
}

// 14px inline icons for the row-action menu — same stroke style as the app.
const I = {
  play: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>`,
  restart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>`,
  admin: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>`,
  term: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>`,
  snapshot: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>`,
  restore: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`,
};

// The action menu for one instance row, gated by running state.
function rowMenuItems(r: Instance): RowMenuItem[] {
  const n = JSON.stringify(r.name);
  const items: RowMenuItem[] = [
    r.running
      ? { label: "Stop", icon: I.stop, js: `sb.act(${n},'stop')` }
      : { label: "Start", icon: I.play, js: `sb.act(${n},'start')` },
    { label: "Restart", icon: I.restart, js: `sb.act(${n},'restart')`, disabled: !r.running },
    { label: "Open admin", icon: I.admin,
      js: `window.open(${JSON.stringify(r.url + "/wp-admin")},'_blank')`, disabled: !r.running },
    { label: "Console", icon: I.term,
      js: `sb.navigate(${JSON.stringify(instancePath(r.name, true))})`, disabled: !r.running },
    { label: "Snapshot", icon: I.snapshot, js: `sb.doSnapshot(${n})` },
    { label: "Restore…", icon: I.restore, js: `sb.doRestore(${n})` },
  ];
  // Every instance is a per-project instance and can be deleted.
  items.push({ label: "Delete", icon: I.trash, js: `sb.doDelete(${n})`, danger: true });
  return items;
}

function listItem(r: Instance): string {
  const sel = r.name === activeInstanceName();
  const dot = r.running ? "bg-emerald-500" : "bg-neutral-300 dark:bg-neutral-600";
  const b = store.busy[r.name];

  // While an action is in flight, the spinner replaces the ⋯ menu trigger.
  const tail = b
    ? `<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`
    : `<span class="ml-auto transition-opacity ${sel ? "opacity-100" : "opacity-0 group-hover:opacity-100"}">
        ${rowMenu("rm-" + r.name, rowMenuItems(r))}</span>`;

  return `<a href="${instancePath(r.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${sel ? "bg-white dark:bg-neutral-800 shadow-sm" : "hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${dot} shrink-0"></span>
     <span class="truncate ${sel ? "font-medium text-neutral-900 dark:text-neutral-50" : "text-neutral-700 dark:text-neutral-300"}">${esc(r.name)}</span>
     ${tail}</a>`;
}

export function renderSidebar(forceHost = false): void {
  // Don't rewrite the list out from under an open row menu (the 5s poll would
  // otherwise close it mid-interaction). Action clicks call rowMenuClose()
  // before re-rendering, so this only ever skips a passive poll tick.
  if (document.querySelector("[data-rowmenu-pop]:not(.hidden)")) return;
  // Keep an open keyboard/mouse host menu stable during passive polling. Full
  // route renders pass forceHost so back/forward navigation stays accurate.
  if (!forceHost && document.querySelector("#hostSelector[open]")) return;
  const items = store.data.instances;
  const active = currentRoute();
  const activeHost = active.page === "instance" || active.page === "create" ? "local"
    : active.page === "remote" || active.page === "remote-instance" ? active.name : "all";
  $("hostSelectorSlot").innerHTML = sidebarHostSelector(activeHost);
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
      r.project, !!r.pending, r.url, r.domain || "",
      store.busy[r.name] || "", store.data.plugins.length]);
  }
  if (route.page === "usage") return "usage:" + (store.usage ? "loaded" : "pending");
  if (route.page === "remote" || route.page === "remote-instance") return route.page + ":" + route.name + ":" + (route.page === "remote-instance" ? route.instance : "") + ":" + !!store.remoteBusy[route.name] + ":" + JSON.stringify(store.remote[route.name] || null);
  if (route.page === "home") return "home:" + store.sync.refreshing + ":" + store.sync.lastCompleted + ":" + store.sync.error + ":" + JSON.stringify(store.data.remotes) + ":" + JSON.stringify(store.remote);
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
    case "create": return createView();
    case "usage": return usageView();
    case "remote": return remoteView(route.name, store.remote[route.name]);
    case "remote-instance": return remoteView(route.name, store.remote[route.name], route.instance);
    case "instance": {
      const r = store.data.instances.find((i) => i.name === route.name) || null;
      return instanceView(r);
    }
    case "home": return hostsView();
    default: return hostsView();
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
export function render(): void { renderSidebar(true); renderDetail(true); }
