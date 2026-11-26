import { esc } from "../dom";
import type { RemoteInventory, ResourceRow } from "../types";
import { remoteInstancePath, remotePath } from "../router";
import { remoteLabel, store } from "../state";
import { theme } from "../theme";
import { hostRail } from "./hosts";
import { hostedTargetsSection } from "./hosting";
import { CPU_LABEL, CPU_TITLE } from "../ui/hostlabels";

const bytes = (value?: number | null): string => {
  if (value == null) return "unknown";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let n = Math.max(0, value), unit = 0;
  while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
  return `${n.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
};
const number = (value?: number | null): string =>
  value == null || !Number.isFinite(value) ? "unknown" : String(value);
const metric = (label: string, value: string, note = "") => `<div class="${theme.panel} p-4">
  <div class="${theme.label}">${esc(label)}</div>
  <div class="mt-1 text-[20px] font-semibold tabular-nums text-neutral-900 dark:text-white">${esc(value)}</div>
  ${note ? `<div class="mt-1 text-[11px] ${theme.quiet}">${esc(note)}</div>` : ""}</div>`;

function table(title: string, rows: ResourceRow[], columns: Array<[string, string, (row: ResourceRow) => string]>, empty: string): string {
  const body = rows.length ? rows.map(row => `<tr class="border-b border-brd/70 dark:border-brd-dark/70 last:border-0">${columns.map(([, , format]) => `<td class="py-2.5 pr-4 text-[12px] text-neutral-600 dark:text-neutral-300">${esc(format(row))}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${columns.length}" class="py-4 text-[12px] text-neutral-400">${esc(empty)}</td></tr>`;
  return `<section class="${theme.panel} p-4"><div class="flex items-center justify-between gap-3 mb-3"><h2 class="text-[13px] font-semibold">${esc(title)}</h2><span class="text-[11px] ${theme.quiet}">${rows.length} observed</span></div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${columns.map(([key, label]) => `<th scope="col" class="pb-2 pr-4 ${theme.label}">${esc(label || key)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div></section>`;
}

export function remoteView(name: string, data?: RemoteInventory, selectedInstance?: string): string {
  if (!data) return `<div class="${theme.page}"><div class="${theme.shell}">${hostRail(name)}<div class="py-16 text-center text-neutral-500 dark:text-neutral-400 text-[13px]">Loading ${esc(name)}…</div></div></div>`;
  const instances = data.instances || { total: 0, running: 0, stopped: 0, rows: [] };
  const host = data.host || {};
  const storage = data.storage;
  const stateClass = data.evidence_status === "complete" ? "text-emerald-600" : data.evidence_status === "partial" ? "text-amber-600" : "text-rose-600";
  const diskPct = host.disk_total_bytes ? Math.min(100, Math.max(0, (host.disk_used_bytes || 0) * 100 / host.disk_total_bytes)) : 0;
  const perInstance = (data.per_instance_usage || []) as ResourceRow[];
  const instanceUsageRows = instances.rows.map(instance => {
    const usage = perInstance.find(row => row.name === instance.name);
    return { ...usage, name: instance.name, running: instance.running };
  });
  const apps = (data.process_view?.apps || []) as ResourceRow[];
  const processes = (data.process_view?.processes || []) as ResourceRow[];
  const containers = (data.containers?.rows || []) as ResourceRow[];
  const refresh = JSON.stringify(name);
  const busy = !!store.remoteBusy[name];
  const selectedUsage = selectedInstance ? perInstance.find(row => row.name === selectedInstance) : undefined;
  const selectedMeta = selectedInstance ? instances.rows.find(row => row.name === selectedInstance) : undefined;
  const instanceDetail = selectedInstance ? `<section class="${theme.panel} p-5"><div class="flex items-start justify-between gap-4"><div><div class="${theme.label}">Remote instance · read only</div><h2 class="mt-1 text-[20px] font-semibold">${esc(selectedInstance)}</h2><p class="mt-1 text-[12px] ${theme.quiet}">${esc(selectedMeta?.project || "Project unavailable")} · ${esc(selectedMeta?.server || "server unknown")}</p></div><a href="${remotePath(name)}" data-link class="${theme.button}">Back to host</a></div><div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">${metric("State", selectedMeta ? (selectedMeta.running ? "running" : "stopped") : "unknown")}${metric("RAM", bytes(selectedUsage?.memory_used_bytes))}${metric("CPU", `${number(selectedUsage?.cpu_percent)}%`)}${metric("Containers", number(selectedUsage?.container_count), String(selectedUsage?.attribution_status || "unknown"))}</div><p class="mt-4 text-[12px] ${theme.quiet}">Remote lifecycle controls are not exposed by this inventory endpoint yet. Resource values are container-attributed evidence.</p></section>` : "";
  const storageSection = (() => {
    const rec = storage?.reconciliation;
    const idx = storage?.directory_index;
    const findings = storage?.top_findings || [];
    const reclaim = storage?.reclaim;
    const routine = storage?.routine;
    const routineBroken = !!(routine?.enabled && routine.unit_healthy === false);
    const tierEntries = Object.entries(reclaim?.tiers || {});
    const classRows = reclaim?.classes || [];
    const indexNote = idx
      ? `index ${idx.source || "unknown"}${idx.complete ? " · complete" : " · partial"}${idx.stale ? " · stale" : ""}${idx.age_seconds != null ? ` · ${Math.round(idx.age_seconds / 60)}m old` : ""}`
      : "";
    return `<section class="${theme.panel} p-5 space-y-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div class="${theme.label}">Storage attribution &amp; cleanup</div>
          <p class="mt-1 text-[12px] ${theme.muted}">${esc(storage?.status || "unavailable")} · attribution ${esc(storage?.attribution_status || "unknown")} · used ${esc(bytes(storage?.capacity?.used_bytes))} · available ${esc(bytes(storage?.capacity?.available_bytes))}${indexNote ? ` · ${esc(indexNote)}` : ""}</p>
        </div>
        <button type="button" ${busy ? "disabled" : ""} class="${theme.button}" onclick="sb.openCleanup(${refresh})">Clean up storage</button>
      </div>
      ${routineBroken ? `<div role="alert" class="rounded-lg border border-rose-300 bg-rose-50 px-3.5 py-2.5 text-[12px] text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-100">Scheduled cleanup routine is enabled (${esc(routine?.cadence || "scheduled")}) but its systemd unit is unhealthy${routine?.exec_exists === false ? " (missing binary)" : ""}${routine?.result ? ` · result ${esc(routine.result)}` : ""}. Run <code>sb resources routine --refresh</code> or activate a fixed release.</div>` : ""}
      ${routine ? `<div class="text-[12px] ${theme.quiet}">Routine: ${routine.enabled ? `enabled (${esc(routine.cadence || "scheduled")})` : "disabled"}${routine.exclusions_count ? ` · ${esc(String(routine.exclusions_count))} exclusions` : ""}${routine.last_run?.outcome ? ` · last run ${esc(routine.last_run.outcome)}${routine.last_run.bytes_reclaimed ? ` (${esc(bytes(routine.last_run.bytes_reclaimed))} reclaimed)` : ""}` : ""}</div>` : ""}
      ${rec ? `<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div class="${theme.inset} p-3"><div class="${theme.label}">Accounted</div><div class="mt-1 text-[15px] font-semibold tabular-nums">${esc(bytes(rec.accounted_bytes))}</div></div>
        <div class="${theme.inset} p-3"><div class="${theme.label}">Unexplained</div><div class="mt-1 text-[15px] font-semibold tabular-nums">${esc(bytes(rec.residual_unexplained_bytes))}</div></div>
        <div class="${theme.inset} p-3"><div class="${theme.label}">Indexed dirs</div><div class="mt-1 text-[15px] font-semibold tabular-nums">${esc(bytes(rec.directory_allocated_bytes))}</div></div>
        <div class="${theme.inset} p-3"><div class="${theme.label}">Deleted-open</div><div class="mt-1 text-[15px] font-semibold tabular-nums">${esc(bytes(rec.deleted_open_bytes))}</div></div>
      </div>` : ""}
      ${tierEntries.length ? `<div class="flex flex-wrap gap-2 text-[12px]">${tierEntries.map(([tier, info]) => `<span class="${theme.inset} px-2.5 py-1 tabular-nums"><span class="font-semibold">${esc(tier)}</span>: ${esc(bytes(info?.bytes))} (${esc(String(info?.count ?? 0))})</span>`).join("")}${reclaim?.volumes ? `<span class="${theme.inset} px-2.5 py-1 tabular-nums"><span class="font-semibold">orphaned volumes</span>: ${esc(bytes(reclaim.volumes.eligible_bytes))} (${esc(String(reclaim.volumes.eligible))})</span>` : ""}</div>` : ""}
      ${classRows.length ? `<div><div class="${theme.label} mb-2">Managed deploy-src breakdown</div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr><th class="pb-1.5 pr-4 ${theme.label}">Class</th><th class="pb-1.5 pr-4 ${theme.label}">Count</th><th class="pb-1.5 pr-4 ${theme.label}">Tier</th><th class="pb-1.5 text-right ${theme.label}">Size</th></tr></thead><tbody>${classRows.map(row => `<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="py-1.5 pr-4 text-[12px] font-medium">${esc(row.class)}</td><td class="py-1.5 pr-4 text-[12px] tabular-nums">${esc(String(row.count))}${row.unmeasured ? ` <span class="${theme.quiet}">(${esc(String(row.unmeasured))} unmeasured)</span>` : ""}</td><td class="py-1.5 pr-4 text-[12px] ${theme.quiet}">${esc(row.tier || "protected")}</td><td class="py-1.5 text-right text-[12px] tabular-nums">${esc(bytes(row.bytes))}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
      ${findings.length ? `<div><div class="${theme.label} mb-2">Top storage consumers</div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr><th class="pb-1.5 pr-4 ${theme.label}">Location</th><th class="pb-1.5 pr-4 ${theme.label}">Kind</th><th class="pb-1.5 pr-4 ${theme.label}">Guidance</th><th class="pb-1.5 text-right ${theme.label}">Size</th></tr></thead><tbody>${findings.map(row => `<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="py-1.5 pr-4 font-mono text-[11px] break-all">${esc(row.display_name)}</td><td class="py-1.5 pr-4 text-[12px] ${theme.quiet}">${esc(row.kind)}</td><td class="py-1.5 pr-4 text-[12px] ${theme.quiet}">${esc(row.guidance)}</td><td class="py-1.5 text-right text-[12px] tabular-nums">${esc(bytes(row.observed_bytes))}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
      <p class="text-[11px] ${theme.quiet}">Deep scans are bounded and may remain partial. Unknown bytes are never deleted automatically.</p>
    </section>`;
  })();
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6"><header class="space-y-4">${hostRail(name)}<div class="flex flex-col gap-4 md:flex-row md:items-end"><div class="flex-1"><div class="${theme.label}">Remote host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">${esc(remoteLabel(name))}</h1><p class="mt-1 text-[12px] ${theme.muted}">Authenticated inventory · ${esc(data.scan_mode || "fast")} snapshot</p></div><div class="flex flex-wrap items-center gap-2"><span class="mr-1 text-[12px] font-semibold ${stateClass}">${busy ? "refreshing" : esc(data.evidence_status)}</span><button ${busy ? "disabled" : ""} class="${theme.button}" onclick="sb.refreshRemote(${refresh})">Quick refresh</button><button ${busy ? "disabled" : ""} class="${theme.button}" onclick="sb.openCleanup(${refresh})">Clean up storage</button><button ${busy ? "disabled" : ""} class="${theme.primary}" onclick="sb.refreshRemote(${refresh},true)">Rebuild attribution</button></div></div></header>${data.partial_reasons?.length ? `<div role="status" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Partial evidence: ${esc(data.partial_reasons.join(", "))}. Unknown values are intentionally not treated as zero.</div>` : ""}${instanceDetail}<section class="${theme.panel} p-5"><div class="flex items-end justify-between gap-3"><div><div class="${theme.label}">Host disk capacity</div><div class="mt-1 text-[18px] font-semibold">${bytes(host.disk_used_bytes)} used <span class="font-normal ${theme.quiet}">of ${bytes(host.disk_total_bytes)}</span></div></div><div class="text-right text-[12px] ${theme.quiet}">${bytes(host.disk_free_bytes)} free</div></div><div class="mt-4 h-3 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"><div class="h-full bg-blue-700" style="width:${diskPct}%"></div></div><div class="mt-2 text-[11px] ${theme.quiet}">Container values overlap host capacity and are not additive.</div></section><div class="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">${metric("Sandbox dev instances", String(instances.total), `${instances.running} running · ${instances.stopped} stopped`)}${metric("Hosted apps", data.hosted_targets ? String(data.hosted_targets.total) : "unknown", data.hosted_targets ? `${data.hosted_targets.running ?? "checking…"} with running containers` : "not reported by this host")}${metric("RAM used", host.memory_used_percent == null ? "unknown" : `${host.memory_used_percent}%`, `${number(host.memory_used_mb)} of ${number(host.memory_total_mb)} MiB`)}${metric("Load 1m", number(host.load_1m), "point-in-time host sample")}${metric("Active jobs", number(data.jobs?.active), `${number(data.jobs?.queued)} queued`)}${metric("Containers", number(containers.length), data.containers?.status || "unavailable")}${metric("Storage attribution", storage?.attribution_status || "unknown", storage?.status || "unavailable")}${metric("Unattributed", String(data.unattributed_containers?.length ?? "unknown"), "containers without a confident match")}${metric("Disk pressure", `${diskPct.toFixed(1)}%`, "capacity-backed")}</div><section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Sandbox dev instances on this host</h2><span class="text-[11px] ${theme.quiet}">made with <code>sb up</code> / <code>sb deploy</code>; hosted apps are listed below</span></div><div class="overflow-x-auto ${theme.panel}"><table class="w-full text-left"><thead><tr>${["Instance", "State", "RAM", CPU_LABEL, "Evidence"].map(label => `<th class="p-3 ${theme.label}" ${label === CPU_LABEL ? `title="${esc(CPU_TITLE)}"` : ""}>${label}</th>`).join("")}</tr></thead><tbody>${instanceUsageRows.length ? instanceUsageRows.map(row => `<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="p-3 text-[12px] font-medium"><a data-link class="text-blue-700 hover:underline dark:text-blue-300" href="${remoteInstancePath(name, String(row.name))}">${esc(String(row.name))}</a></td><td class="p-3 text-[12px]">${row.running ? "running" : "stopped"}</td><td class="p-3 text-[12px] tabular-nums">${esc(bytes(row.memory_used_bytes))}</td><td class="p-3 text-[12px] tabular-nums">${esc(number(row.cpu_percent))}%</td><td class="p-3 text-[12px] ${theme.quiet}">${esc(String(row.attribution_status || "unattributed"))}</td></tr>`).join("") : `<tr><td colspan="5" class="p-4 text-[12px] ${theme.quiet}">No Sandbox dev instances on this host.</td></tr>`}</tbody></table></div></section>${hostedTargetsSection(name, data.hosted_targets)}<div class="grid gap-4 xl:grid-cols-3">${table("Apps", apps, [["name", "App", row => String(row.name || "unknown")],["process_count", "Processes", row => number(row.process_count)],["rss_bytes", "RSS", row => bytes(row.rss_bytes)],["cpu_percent", "CPU avg", row => `${number(row.cpu_percent)}%`]], data.process_view?.status || "Process view unavailable")}${table("Processes", processes.slice(0, 50), [["pid", "PID", row => number(row.pid)],["name", "Name", row => String(row.name || "unknown")],["rss_bytes", "RSS", row => bytes(row.rss_bytes)],["cpu_percent", "CPU avg", row => `${number(row.cpu_percent)}%`]], "No process rows")}${table("Containers", containers, [["name", "Container", row => String(row.name || "unknown")],["memory_used_bytes", "Memory", row => bytes(row.memory_used_bytes)],["memory_percent", "Memory %", row => `${number(row.memory_percent)}%`],["cpu_percent", CPU_LABEL, row => `${number(row.cpu_percent)}%`]], data.containers?.status || "Container view unavailable")}</div>${storageSection}</div></div>`;
}
