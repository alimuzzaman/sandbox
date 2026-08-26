// Entry point: wire modules together, expose window.sb for inline handlers,
// initialise the router, and start the single-flight refresh scheduler.

import { $ } from "./dom";
import { store } from "./state";
import { fetchData, fetchRemotes, fetchUsage, fetchRemote } from "./api";
import { navigate, initRouter, onRoute, currentRoute, hostContext, instancePath, remotePath } from "./router";
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
import { doCreate, submitCreate, initCreateView } from "./pages/create";
import type { SbApi } from "./types";

// ---- data refresh scheduler ----
// Network inventory can take tens of seconds. A recursive scheduler plus one
// shared promise ensures a slow request can never be overlapped by the next
// tick, a route change, or a visibility event.
let refreshInFlight: Promise<void> | null = null;
let refreshQueued = false;
const remoteInFlight = new Map<string, Promise<void>>();

function loadRemote(name: string, mode: "fast" | "deep" = "fast"): Promise<void> {
  const active = remoteInFlight.get(name);
  if (active && mode === "fast") return active;
  const run = async (): Promise<void> => {
    if (active) await active;
    store.remoteBusy[name] = true;
    renderSidebar(false);
    renderDetail(false);
    try {
      store.remote[name] = await fetchRemote(name, mode);
    } finally {
      delete store.remoteBusy[name];
      renderSidebar(false);
    }
  };
  const request = run().finally(() => {
    if (remoteInFlight.get(name) === request) remoteInFlight.delete(name);
  });
  remoteInFlight.set(name, request);
  return request;
}

async function performRefresh(): Promise<void> {
  if (store.paused) return;
  store.sync.refreshing = true;
  store.sync.error = null;
  renderDetail(false);
  try {
    // Remote summaries are registry-local and cheap; local instance rows may
    // require one Compose probe per registered project. Fetch them separately
    // so the remote rail can render while the slower local inventory finishes.
    let remoteSummariesLoaded = false;
    const remoteSummaries = fetchRemotes().then(({ remotes }) => {
      remoteSummariesLoaded = true;
      store.data = { ...store.data, remotes };
      renderSidebar(false);
      renderDetail(false);
    });
    const localData = fetchData().then(data => {
      store.data = {
        ...data,
        remotes: remoteSummariesLoaded ? store.data.remotes : data.remotes,
      };
      renderSidebar(false);
      renderDetail(false);
    });
    const results = await Promise.allSettled([remoteSummaries, localData]);
    const failures = results.filter(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    if (failures.length === results.length) throw failures[0].reason;
    if (failures.length) {
      const failure = failures[0].reason;
      store.sync.error = failure instanceof Error ? failure.message : "Refresh incomplete";
    }
    const route = currentRoute();
    if (route.page === "remote" || route.page === "remote-instance") {
      await loadRemote(route.name);
    }
    store.sync.lastCompleted = Date.now();
  } catch (error) {
    store.sync.error = error instanceof Error ? error.message : "Refresh failed";
  } finally {
    store.sync.refreshing = false;
    renderSidebar();
    renderDetail(false);
  }
}

function refresh(queueAfterCurrent = false): Promise<void> {
  if (refreshInFlight) {
    if (queueAfterCurrent) refreshQueued = true;
    return refreshInFlight;
  }
  refreshInFlight = performRefresh().finally(() => {
    refreshInFlight = null;
    if (refreshQueued) {
      refreshQueued = false;
      void refresh();
    }
  });
  return refreshInFlight;
}

// ---- page-level handlers ----
async function showUsage(): Promise<void> {
  if (hostContext().kind !== "local") {
    toast("Agent usage is available only for the local host", "err");
    return;
  }
  navigate("/usage");
  $("detail").innerHTML = `<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading agent usage…</div>`;
  try { store.usage = await fetchUsage(); } catch { store.usage = { available: false }; }
  renderDetail(true);
}

function goHome(): void { navigate("/"); }
function selectInstance(name: string): void { navigate(instancePath(name)); }
function selectRemote(name: string): void { navigate(remotePath(name)); }
function requireLocal(action: string): boolean {
  if (hostContext().kind === "local") return true;
  toast(`${action} is available only for the local host`, "err");
  return false;
}
async function refreshRemote(name: string, deep = false): Promise<void> {
  try {
    await loadRemote(name, deep ? "deep" : "fast");
    renderDetail(true);
  } catch {
    toast("remote inventory refresh failed", "err");
  }
}

async function refreshHosts(): Promise<void> {
  const names = store.data.remotes.filter(remote => remote.control_ready).map(remote => remote.name);
  const outcomes = await Promise.allSettled(names.map(name => loadRemote(name)));
  const failed = outcomes.filter(outcome => outcome.status === "rejected").length;
  if (failed) toast(`${failed} host ${failed === 1 ? "refresh" : "refreshes"} failed`, "err");
  renderDetail(true);
}

function showHelp(): void {
  modal({
    title: "How AI agents work here", okText: "Got it",
    desc: "The sandbox gives Codex, Claude, and other connected agents a live WordPress to act in, so they can verify " +
      "instead of guess: run WP-CLI, hit REST + the DB, open pages in a real " +
      "browser, read/edit your plugin's code, and tail logs. Say \"focus <plugin>\" " +
      "or \"work on <plugin>\" in chat — the agent picks the matching environment, " +
      "symlinks the plugin in, loads its code + context, and can build, reproduce, " +
      "and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — " +
      "each tool takes the project directory and resolves the right environment from " +
      "the registry. Open an environment on the left for its exact snippet. It's " +
      "real WordPress — break it freely, snapshot or delete anytime.",
  });
}

// ---- expose the inline-handler surface ----
const sb: SbApi & { copyText: (t: string, b: HTMLElement) => void } = {
  navigate, goHome, selectInstance, selectRemote, refreshRemote, refreshHosts, showUsage, showHelp, openTerminal,
  submitCreate,
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
  // Mutations request one follow-up refresh if a passive poll is already in
  // flight. Passive navigation/visibility events simply share that request.
  setActionDeps({ refresh: () => refresh(true), render });
  setConsoleRefresh(() => refresh(true));
  initModal();
  initConsole();
  initCselOutsideClose();
  initRowMenuClose();
  initRouter();

  // Static sidebar buttons (not data-link).
  ($("newBtn") as HTMLButtonElement).onclick = doCreate;
  ($("startAll") as HTMLButtonElement).onclick = () => { if (requireLocal("Start all")) act("*", "start-all"); };
  ($("stopAll") as HTMLButtonElement).onclick = () => { if (requireLocal("Stop all")) act("*", "stop-all"); };
  ($("usageBtn") as HTMLAnchorElement).onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    void showUsage();
  };
  ($("helpBtn") as HTMLButtonElement).onclick = showHelp;
  ($("termBtn") as HTMLButtonElement).onclick = () => {
    if (!requireLocal("Terminal")) return;
    const inst = activeInstanceName() || (store.data.instances[0] && store.data.instances[0].name);
    if (!inst) { toast("create an instance first", "err"); return; }
    navigate(instancePath(inst, true));
    openTerminal(inst);
  };

  // Re-render whenever the route changes (link clicks, back/forward, navigate()).
  onRoute((route) => {
    render();
    if (route.page === "create") initCreateView();
    if (route.page === "remote" || route.page === "remote-instance" || route.page === "home" || route.page === "local-host") {
      void refresh();
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
  if (currentRoute().page === "create") initCreateView();
  const r0 = currentRoute();
  if (r0.page === "instance" && r0.console) openTerminal(r0.name);
  startPolling();
}

// Wait 30s after a completed refresh before polling again. A 25s remote scan
// therefore produces roughly one request per minute, never stacked requests.
const POLL_MS = 30000;
let pollTimer = 0;
function startPolling(): void {
  const schedule = (): void => {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      if (document.visibilityState === "visible") await refresh();
      schedule();
    }, POLL_MS);
  };
  void refresh().finally(schedule);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" &&
        (!store.sync.lastCompleted || Date.now() - store.sync.lastCompleted > POLL_MS)) {
      void refresh();
    }
  });
}

boot();
