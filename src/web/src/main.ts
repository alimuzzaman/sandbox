// Entry point: wire modules together, expose window.sb for inline handlers,
// initialise the router, and start the 2s refresh tick.

import { $ } from "./dom";
import { store } from "./state";
import { fetchData, fetchUsage } from "./api";
import { navigate, initRouter, onRoute, currentRoute, instancePath } from "./router";
import { render, renderSidebar, renderDetail, activeInstanceName } from "./render";
import { initModal, modal } from "./ui/modal";
import { cselToggle, cselPick, cselFilter, initCselOutsideClose } from "./ui/csel";
import {
  initConsole, setConsoleRefresh, consoleClose, openTerminal,
} from "./ui/console";
import { toast } from "./ui/toast";
import {
  act, op, doFocus, doDelete, doSnapshot, doRestore, doSeed, doWp, doInstall,
  plugFilter, copyText, loadUsageThenRender, setActionDeps,
} from "./actions";
import { doCreate, syncDomainFromName, domainEdited } from "./pages/create";
import type { SbApi } from "./types";

// ---- data refresh tick ----
async function refresh(): Promise<void> {
  if (store.paused) return;
  let d;
  try { d = await fetchData(); } catch { return; }
  store.data = d;
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

function showHelp(): void {
  modal({
    title: "How Claude works here", okText: "Got it",
    desc: "The sandbox gives Claude a live WordPress to act in, so it can verify " +
      "instead of guess: run WP-CLI, hit REST + the DB, open pages in a real " +
      "browser, read/edit your plugin's code, and tail logs. Say \"focus <plugin>\" " +
      "or \"work on <plugin>\" in chat — Claude picks the matching environment, " +
      "symlinks the plugin in, loads its code + context, and can build, reproduce, " +
      "and fix end-to-end. Each environment also has its own tool namespace " +
      "(mcp__sandbox__* = main, mcp__sandbox-<name>__* = that one) so parallel " +
      "sessions never collide. Open an environment on the left for its exact " +
      "snippet. It's real WordPress — break it freely, snapshot or delete anytime.",
  });
}

// ---- expose the inline-handler surface ----
const sb: SbApi & { copyText: (t: string, b: HTMLElement) => void } = {
  navigate, goHome, selectInstance, showUsage, showHelp, openTerminal,
  doCreate, doDelete, doFocus, doSnapshot, doRestore, doSeed, doWp, doInstall,
  plugFilter: () => plugFilter(activeInstanceName()),
  loadUsageThenRender,
  act, op,
  syncDomainFromName, domainEdited,
  cselToggle, cselPick, cselFilter,
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
  refresh();
  setInterval(refresh, 2000);
}

boot();
