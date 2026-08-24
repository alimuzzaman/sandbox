import { esc } from "../dom";
import { store } from "../state";
import { instancePath, localHostPath, remotePath } from "../router";
import { theme } from "../theme";

type HostKind = "all" | "local" | "remote";

function hostIcon(kind: HostKind, ready = true): string {
  const dot = kind === "all" ? "bg-blue-500" : ready ? "bg-emerald-500" : "bg-amber-400";
  const color = kind === "all" ? "text-blue-600 dark:text-blue-400"
    : kind === "local" ? "text-emerald-600 dark:text-emerald-400"
    : "text-blue-700 dark:text-blue-300";
  const shape = kind === "all"
    ? `<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>`
    : kind === "local"
      ? `<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>`
      : `<rect x="4" y="3" width="16" height="8" rx="2"/><rect x="4" y="13" width="16" height="8" rx="2"/><path d="M8 7h.01M8 17h.01"/>`;
  return `<span class="host-icon relative flex shrink-0 items-center justify-center rounded-lg bg-white dark:bg-neutral-800 ${color}">
    <svg aria-hidden="true" class="h-4 w-4" viewBox="0 0 24 24" fill="${kind === "all" ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${shape}</svg>
    <span aria-hidden="true" class="host-dot absolute rounded-full border-neutral-100 dark:border-neutral-950 ${dot}"></span>
  </span>`;
}

export function sidebarHostSelector(active = "all"): string {
  const activeRemote = store.data.remotes.find(remote => remote.name === active);
  const currentKind: HostKind = active === "local" ? "local" : activeRemote ? "remote" : "all";
  const currentName = currentKind === "local" ? "Local host" : activeRemote?.name || "All hosts";
  const currentReady = currentKind !== "remote" || !!activeRemote?.control_ready;
  const currentStatus = currentKind === "all" ? "Host overview"
    : currentKind === "local" ? "This machine"
    : currentReady ? "Remote available" : "Remote unavailable";
  const optionClass = "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-[13px] hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-800";
  const option = (href: string, name: string, kind: HostKind, ready: boolean, status: string, selected: boolean): string =>
    `<a href="${href}" data-link ${selected ? 'aria-current="page"' : ""} class="${optionClass} ${selected ? "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-100" : "text-neutral-700 dark:text-neutral-200"}">
      ${hostIcon(kind, ready)}<span class="min-w-0 flex-1"><span class="block truncate font-medium">${esc(name)}</span><span class="block truncate text-[10px] text-neutral-400">${esc(status)}</span></span>
    </a>`;
  const remotes = store.data.remotes.map(remote => option(
    remotePath(remote.name), remote.name, "remote", remote.control_ready,
    remote.control_ready ? "Remote available" : "Remote unavailable", active === remote.name,
  )).join("");
  return `<details class="group relative" id="hostSelector">
    <summary aria-label="Choose host. Current host: ${esc(currentName)}" class="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-neutral-200/60 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-900">
      ${hostIcon(currentKind, currentReady)}
      <span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-semibold text-neutral-900 dark:text-neutral-50">${esc(currentName)}</span><span class="block truncate text-[10px] text-neutral-400">${esc(currentStatus)}</span></span>
      <svg aria-hidden="true" class="h-4 w-4 shrink-0 text-neutral-400" viewBox="0 0 24 24" fill="none"><path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </summary>
    <nav aria-label="Hosts" class="absolute left-0 right-0 z-50 mt-1 max-h-64 space-y-0.5 overflow-auto rounded-lg border border-neutral-200 bg-white p-2 shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
      ${option("/", "All hosts", "all", true, "Host overview", active === "all")}
      ${option(localHostPath(), "Local host", "local", true, "This machine", active === "local")}${remotes}
    </nav>
  </details>`;
}

function hostCard(name: string, href: string, kind: string, ready: boolean,
                  total: string, running: string, memory: string, note: string): string {
  return `<a href="${href}" data-link class="${theme.panel} group block p-5 hover:border-blue-300 dark:hover:border-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
    <div class="flex items-start gap-3">
      <span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${ready ? "bg-emerald-500" : "bg-amber-400"}"></span>
      <div class="min-w-0 flex-1"><div class="${theme.label}">${esc(kind)}</div>
        <h2 class="mt-1 truncate text-[16px] font-semibold text-neutral-900 dark:text-white">${esc(name)}</h2></div>
      <span aria-hidden="true" class="text-neutral-400 group-hover:text-blue-600">→</span>
    </div>
    <div class="mt-5 grid grid-cols-3 gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
      <div><div class="${theme.label}">Instances</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${esc(total)}</div></div>
      <div><div class="${theme.label}">Running</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${esc(running)}</div></div>
      <div><div class="${theme.label}">RAM</div><div class="mt-1 text-[14px] font-semibold tabular-nums">${esc(memory)}</div></div>
    </div>
    <p class="mt-4 text-[12px] ${theme.quiet}">${esc(note)}</p>
  </a>`;
}

// Kept as a compatibility shim for the remote page while host navigation lives
// in the persistent sidebar. Returning no markup prevents duplicate selectors.
export function hostRail(_active = "all"): string { return ""; }

export function hostsView(): string {
  const localTotal = store.data.instances.length;
  const localRunning = store.data.instances.filter(instance => instance.running).length;
  const remoteCards = store.data.remotes.map(remote => {
    const data = store.remote[remote.name];
    const busy = store.remoteBusy[remote.name];
    const total = data?.instances ? String(data.instances.total) : busy ? "…" : "—";
    const running = data?.instances ? String(data.instances.running) : busy ? "…" : "—";
    const memory = data?.host?.memory_used_percent == null ? "—" : `${data.host.memory_used_percent}%`;
    const note = busy ? "Refreshing host inventory…" : data
      ? `${data.evidence_status} ${data.scan_mode || "fast"} evidence`
      : remote.control_ready ? "Waiting for first inventory" : "Control service unavailable";
    return hostCard(remote.name, remotePath(remote.name), "Remote host", remote.control_ready,
      total, running, memory, note);
  }).join("");
  const remoteInstances = store.data.remotes.reduce((sum, remote) =>
    sum + (store.remote[remote.name]?.instances?.total || 0), 0);
  const knownHosts = 1 + store.data.remotes.length;
  const status = store.sync.refreshing ? "Refreshing inventories…" : store.sync.lastCompleted
    ? `Updated ${new Date(store.sync.lastCompleted).toLocaleTimeString()}` : "Loading inventories…";

  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">
    <header class="space-y-4"><div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div class="${theme.label}">Host control</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">All Sandbox hosts</h1>
      <p class="mt-1 text-[13px] ${theme.muted}">One view of local and remote WordPress capacity.</p></div>
      <div class="flex flex-wrap items-center justify-end gap-3"><div role="status" class="text-[12px] ${store.sync.error ? "text-red-700 dark:text-red-300" : theme.quiet}">${esc(store.sync.error || status)}</div><button class="${theme.button}" onclick="sb.refreshHosts()">Refresh remote hosts</button></div>
    </div></header>
    <section class="${theme.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Hosts", knownHosts], ["Known instances", localTotal + remoteInstances], ["Local running", localRunning], ["Remote hosts", store.data.remotes.length]].map(([label, value]) => `<div class="bg-white p-4 dark:bg-neutral-900"><div class="${theme.label}">${label}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${value}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Available hosts</h2><span class="text-[11px] ${theme.quiet}">Select a host to inspect its instances</span></div>
      <div class="grid gap-4 lg:grid-cols-2">${hostCard("Local host", localHostPath(), "This machine", true, String(localTotal), String(localRunning), "—", "Open local instances and lifecycle controls")}${remoteCards}</div>
    </section>
  </div></div>`;
}

export function localHostView(): string {
  const instances = store.data.instances;
  const running = instances.filter(instance => instance.running).length;
  const pending = instances.filter(instance => instance.pending).length;
  const stopped = Math.max(0, instances.length - running - pending);
  const rows = instances.length ? instances.map(instance => `<a href="${instancePath(instance.name)}" data-link class="flex items-center gap-3 border-t border-neutral-200 px-4 py-3 first:border-t-0 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500 dark:border-neutral-800 dark:hover:bg-neutral-800/50">
    <span class="h-2 w-2 shrink-0 rounded-full ${instance.running ? "bg-emerald-500" : instance.pending ? "bg-amber-400" : "bg-neutral-300 dark:bg-neutral-600"}"></span>
    <span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-medium text-neutral-900 dark:text-white">${esc(instance.name)}</span><span class="block truncate text-[11px] ${theme.quiet}">${esc(instance.project || "Local project")} · ${esc(instance.server || "server unknown")}</span></span>
    <span class="text-[11px] ${theme.quiet}">${instance.pending ? "pending" : instance.running ? "running" : "stopped"}</span><span aria-hidden="true" class="text-neutral-400">→</span>
  </a>`).join("") : `<div class="p-6 text-center text-[13px] ${theme.quiet}">No local instances yet. Use New instance to create one from a local project.</div>`;

  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">
    <header><div class="${theme.label}">Local host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">This machine</h1><p class="mt-1 text-[13px] ${theme.muted}">Local Sandbox instances and lifecycle controls.</p></header>
    <section class="${theme.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Instances", instances.length], ["Running", running], ["Stopped", stopped], ["Pending", pending]].map(([label, value]) => `<div class="bg-white p-4 dark:bg-neutral-900"><div class="${theme.label}">${label}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${value}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between gap-3"><h2 class="text-[14px] font-semibold">Local instances</h2><a href="/create" data-link class="${theme.primary}">New instance</a></div><div class="${theme.panel} overflow-hidden">${rows}</div></section>
  </div></div>`;
}
