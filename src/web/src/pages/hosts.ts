import { esc } from "../dom";
import { store } from "../state";
import { instancePath, remotePath } from "../router";
import { theme } from "../theme";

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

export function hostRail(active = "all"): string {
  const remotes = store.data.remotes.map(remote =>
    `<a href="${remotePath(remote.name)}" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${active === remote.name ? "border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100" : "border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full ${remote.control_ready ? "bg-emerald-500" : "bg-amber-400"}"></span>${esc(remote.name)}</a>`).join("");
  const localHref = store.data.instances[0] ? instancePath(store.data.instances[0].name) : "/create";
  return `<nav aria-label="Host selector" class="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
    <a href="/" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${active === "all" ? "border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100" : "border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full bg-blue-500"></span>All hosts</a>
    <a href="${localHref}" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${active === "local" ? "border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100" : "border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-500"></span>Local host</a>${remotes}
  </nav>`;
}

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
    </div>${hostRail("all")}</header>
    <section class="${theme.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Hosts", knownHosts], ["Known instances", localTotal + remoteInstances], ["Local running", localRunning], ["Remote hosts", store.data.remotes.length]].map(([label, value]) => `<div class="bg-white p-4 dark:bg-neutral-900"><div class="${theme.label}">${label}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${value}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Available hosts</h2><span class="text-[11px] ${theme.quiet}">Select a host to inspect its instances</span></div>
      <div class="grid gap-4 lg:grid-cols-2">${hostCard("Local host", store.data.instances[0] ? instancePath(store.data.instances[0].name) : "/create", "This machine", true, String(localTotal), String(localRunning), "—", "Open local instances and lifecycle controls")}${remoteCards}</div>
    </section>
  </div></div>`;
}
