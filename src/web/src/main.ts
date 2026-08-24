// Entry point: wire modules together, expose window.sb for inline handlers,
// initialise the router, and start the 2s refresh tick.

import { $ } from "./dom";
import { store } from "./state";
import { fetchData, fetchUsage, fetchRemote } from "./api";
import { navigate, initRouter, onRoute, currentRoute, instancePath, remotePath } from "./router";
import { render, renderSidebar, renderDetail, activeInstanceName } from "./render";
import { initModal, modal } from "./ui/modal";
import { cselToggle, cselPick, cselFilter, initCselOutsideClose } from "./ui/csel";
import { rowMenuToggle, rowMenuClose, initRowMenuClose } from "./ui/rowmenu";
import {
  initConsole, setConsoleRefresh, consoleClose, openTerminal,
} from "./ui/console";
import { toast } from "./ui/toast";
import {
  act, op, doFocus, doServer, doDelete, doSnapshot, doRestore, doSeed, doWp, doInstall,
  plugFilter, copyText, loadUsageThenRender, setActionDeps,
} from "./actions";
import { doCreate } from "./pages/create";
import type { SbApi } from "./types";

// ---- data refresh tick ----
async function refresh(): Promise<void> {
  if (store.paused) return;
  let d;
  try { d = await fetchData(); } catch { return; }
  store.data = d;
  const route = currentRoute();
  if (route.page === "remote") {
    try { store.remote[route.name] = await fetchRemote(route.name); } catch { /* retain prior evidence */ }
  }
  renderSidebar();
  renderDetail(false); // soft: only if changed + idle
}

// ---- page-level handlers ----
async function showUsage(): Promise<void> {
  navigate("/usage");
  $("detail").innerHTML = `<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>`;
  try { store.usage = await fetchUsage(); } catch { store.usage = { available: false }; }
  renderDetail(true);
}

function goHome(): void { navigate("/"); }
function selectInstance(name: string): void { navigate(instancePath(name)); }
function selectRemote(name: string): void { navigate(remotePath(name)); }
async function refreshRemote(name: string, deep = false): Promise<void> {
  try {
    store.remote[name] = await fetchRemote(name, deep ? "deep" : "fast");
    renderDetail(true);
  } catch {
    toast("remote inventory refresh failed", "err");
  }
}

function showHelp(): void {
  modal({
    title: "How Claude works here", okText: "Got it",
    desc: "The sandbox gives Claude a live WordPress to act in, so it can verify " +
      "instead of guess: run WP-CLI, hit REST + the DB, open pages in a real " +
      "browser, read/edit your plugin's code, and tail logs. Say \"focus <plugin>\" " +
      "or \"work on <plugin>\" in chat — Claude picks the matching environment, " +
      "symlinks the plugin in, loads its code + context, and can build, reproduce, " +
      "and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — " +
      "each tool takes the project directory and resolves the right environment from " +
      "the registry. Open an environment on the left for its exact snippet. It's " +
      "real WordPress — break it freely, snapshot or delete anytime.",
  });
}

// ---- expose the inline-handler surface ----
const sb: SbApi & { copyText: (t: string, b: HTMLElement) => void } = {
  navigate, goHome, selectInstance, selectRemote, refreshRemote, showUsage, showHelp, openTerminal,
  doCreate, doDelete, doFocus, doServer, doSnapshot, doRestore, doSeed, doWp, doInstall,
  plugFilter: () => plugFilter(activeInstanceName()),
  loadUsageThenRender,
  act, op,
  cselToggle, cselPick, cselFilter,
  rowMenuToggle, rowMenuClose,
  consoleClose,
  copyText,
};
window.sb = sb;

// ---- boot ----
function boot(): void {
  setActionDeps({ refresh, render });
  setConsoleRefresh(refresh);
  initModal();
  initConsole();
  initCselOutsideClose();
  initRowMenuClose();
  initRouter();

  // Static sidebar buttons (not data-link).
  ($("newBtn") as HTMLButtonElement).onclick = doCreate;
  ($("startAll") as HTMLButtonElement).onclick = () => act("*", "start-all");
  ($("stopAll") as HTMLButtonElement).onclick = () => act("*", "stop-all");
  ($("helpBtn") as HTMLButtonElement).onclick = showHelp;
  ($("termBtn") as HTMLButtonElement).onclick = () => {
    const inst = activeInstanceName() || (store.data.instances[0] && store.data.instances[0].name);
    if (!inst) { toast("create an instance first", "err"); return; }
    navigate(instancePath(inst, true));
    openTerminal(inst);
  };

  // Re-render whenever the route changes (link clicks, back/forward, navigate()).
  onRoute((route) => {
    render();
    if (route.page === "remote") {
      fetchRemote(route.name).then(data => { store.remote[route.name] = data; renderDetail(true); }).catch(() => {});
    }
    if (route.page === "instance" && route.console) {
      // Deep-linked console: open the terminal for that instance.
      openTerminal(route.name);
    } else {
      consoleClose();
    }
  });

  // Initial paint from current URL, then start polling.
  render();
  const r0 = currentRoute();
  if (r0.page === "instance" && r0.console) openTerminal(r0.name);
  startPolling();
}

// Live status polling that's cheap when idle: refresh every 5s, but ONLY while
// the tab is visible. Hidden tabs poll nothing; becoming visible triggers an
// immediate refresh so the view is fresh the moment you look at it.
const POLL_MS = 5000;
function startPolling(): void {
  refresh();
  window.setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh(); // catch up at once
  });
}

boot();
