// Confirmation-gated storage cleanup modal for a host page (/remote/<name>).
//
// Lives on `document.body` outside the re-rendered app root so background
// inventory polling never wipes the reviewed cleanup plan before confirmation.

import { esc } from "../dom";
import { postRemoteCleanup } from "../api";
import { remoteLabel } from "../state";
import { theme } from "../theme";
import { toast } from "../ui/toast";
import type {
  CleanupApplyResult,
  CleanupPlanResult,
  CleanupRefused,
  CleanupTier,
} from "../types";

const openCleanupDialogs = new Set<HTMLElement>();
const dialogFocus = new WeakMap<HTMLElement, HTMLElement>();

const formatBytes = (value?: number | null): string => {
  if (value == null || !Number.isFinite(value)) return "unknown";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let n = Math.max(0, value);
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit++;
  }
  return `${n.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
};

const TIER_INFO: Record<CleanupTier, { label: string; desc: string }> = {
  safe: {
    label: "Safe",
    desc: "Expired and finished-idle workspaces plus orphaned volumes.",
  },
  tmp: {
    label: "Tmp",
    desc: "Safe targets plus temporary scratch directories.",
  },
  all: {
    label: "All expired",
    desc: "Safe and tmp targets plus expired one-shot base checkouts and stopped expired workspaces.",
  },
};

function closeDialog(el: HTMLElement): void {
  el.remove();
  openCleanupDialogs.delete(el);
  const previous = dialogFocus.get(el);
  if (previous?.isConnected) previous.focus();
}

function createDialog(title: string): { el: HTMLElement; body: HTMLElement } {
  for (const other of [...openCleanupDialogs]) closeDialog(other);
  const el = document.createElement("div");
  if (document.activeElement instanceof HTMLElement) dialogFocus.set(el, document.activeElement);
  el.className = "fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-label", title);
  el.innerHTML = `<div class="flex max-h-[85vh] w-full max-w-2xl flex-col gap-3 overflow-y-auto rounded-lg border border-brd bg-app p-5 shadow-xl dark:border-brd-dark dark:bg-card-dark">
    <div class="flex items-start justify-between gap-3">
      <h2 class="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50">${esc(title)}</h2>
      <button type="button" data-close class="${theme.button}">Close</button>
    </div>
    <div data-body class="flex flex-col gap-3 text-[13px] text-neutral-700 dark:text-neutral-200"></div>
  </div>`;
  el.addEventListener("click", (e) => {
    if (e.target === el || (e.target as HTMLElement).closest("[data-close]")) closeDialog(el);
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeDialog(el);
    }
    if (e.key === "Tab") {
      const controls = [...el.querySelectorAll<HTMLElement>("button,input,a[href],[tabindex]")]
        .filter(control => control.tabIndex >= 0 && !control.matches(":disabled") && control.getClientRects().length > 0);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    }
  });
  document.body.appendChild(el);
  openCleanupDialogs.add(el);
  (el.querySelector("[data-close]") as HTMLElement).focus();
  return { el, body: el.querySelector("[data-body]") as HTMLElement };
}

function tierTabs(current: CleanupTier, disabled: boolean): string {
  const tiers: CleanupTier[] = ["safe", "all"];
  return `<div class="flex flex-wrap items-center gap-2" role="tablist" aria-label="Cleanup tier">
    ${tiers.map(tier => {
      const active = tier === current;
      const cls = active ? theme.primary : theme.button;
      return `<button type="button" role="tab" aria-selected="${active ? "true" : "false"}" data-tier="${tier}" ${disabled ? "disabled" : ""} class="${cls}">${esc(TIER_INFO[tier].label)}</button>`;
    }).join("")}
  </div>
  <p class="text-[12px] ${theme.quiet}">${esc(TIER_INFO[current].desc)}</p>`;
}

function renderFailure(result: CleanupRefused): string {
  return `<div role="alert" class="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-900 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-100">
    <div class="font-medium"><code>${esc(result.code)}</code></div>
    <div class="mt-1 text-[12px]">${esc(result.message)}</div>
  </div>`;
}

function renderPlanView(plan: CleanupPlanResult, busy: boolean): string {
  const incomplete = !plan.deployment_inventory_complete || !plan.engine_complete;
  const canApply = !busy && plan.candidate_count > 0 && !incomplete;
  const skippedEntries = Object.entries(plan.skipped_reasons || {});
  const applyClass = plan.tier === "all"
    ? "inline-flex min-h-9 items-center justify-center rounded-lg bg-rose-700 px-3 py-2 text-[12px] font-semibold text-white hover:bg-rose-800 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-rose-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950"
    : `${theme.primary} disabled:opacity-50`;
  const rows = plan.candidates.map(item => `<tr class="border-t border-neutral-200 dark:border-neutral-800">
    <td class="py-1.5 pr-2 font-mono text-[11px] break-all">${esc(item.display_name)}</td>
    <td class="py-1.5 pr-2 text-[11px]">${esc(item.class)}</td>
    <td class="py-1.5 pr-2 text-[11px] ${theme.quiet}">${esc(item.reason)}</td>
    <td class="py-1.5 text-right text-[11px] tabular-nums">${esc(formatBytes(item.bytes))}</td>
  </tr>`).join("");

  return `<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div class="${theme.inset} p-2.5"><div class="${theme.label}">Reclaimable</div><div class="mt-0.5 text-[15px] font-semibold tabular-nums">${esc(formatBytes(plan.estimated_reclaimable_bytes))}</div></div>
      <div class="${theme.inset} p-2.5"><div class="${theme.label}">Candidates</div><div class="mt-0.5 text-[15px] font-semibold tabular-nums">${esc(String(plan.candidate_count))}</div></div>
      <div class="${theme.inset} p-2.5"><div class="${theme.label}">Protected / skipped</div><div class="mt-0.5 text-[15px] font-semibold tabular-nums">${esc(String(plan.skipped_count))}</div></div>
      <div class="${theme.inset} p-2.5"><div class="${theme.label}">Inventory</div><div class="mt-0.5 text-[13px] font-semibold">${esc(plan.inventory_status)}</div></div>
    </div>
    ${incomplete ? `<div role="alert" class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
      Inventory evidence is incomplete (${!plan.deployment_inventory_complete ? "deployment inventory" : "container engine"}). Cleanup apply is blocked until a complete scan succeeds.
    </div>` : ""}
    ${plan.unmeasured_count > 0 ? `<p class="text-[11px] ${theme.quiet}">${esc(String(plan.unmeasured_count))} entries had unmeasured sizes and were kept safe. Run “Rebuild attribution” to refresh the size index.</p>` : ""}
    ${plan.candidates.length ? `<div class="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
      <table class="w-full text-left">
        <thead><tr class="bg-neutral-50 dark:bg-neutral-950">
          <th class="p-2 ${theme.label}">Target</th>
          <th class="p-2 ${theme.label}">Class</th>
          <th class="p-2 ${theme.label}">Reason</th>
          <th class="p-2 text-right ${theme.label}">Size</th>
        </tr></thead>
        <tbody class="px-2">${rows}</tbody>
      </table>
    </div>${plan.candidates_truncated ? `<p class="text-[11px] ${theme.quiet}">Showing first 50 of ${esc(String(plan.candidate_count))} candidates.</p>` : ""}`
      : `<p class="text-[12px] ${theme.quiet}">No eligible cleanup candidates in the ${esc(plan.tier)} tier.</p>`}
    ${skippedEntries.length ? `<p class="text-[11px] ${theme.quiet}">Skipped reasons: ${esc(skippedEntries.map(([reason, count]) => `${reason} (${count})`).join(" · "))}</p>` : ""}
    <div class="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
      <button type="button" data-rescan ${busy ? "disabled" : ""} class="${theme.button}">Re-scan</button>
      <button type="button" data-apply ${canApply ? "" : "disabled"} class="${applyClass}">
        ${busy ? "Cleaning…" : `Confirm & clean up (${formatBytes(plan.estimated_reclaimable_bytes)})`}
      </button>
    </div>`;
}

function renderApplyResult(result: CleanupApplyResult): string {
  const tone = result.failed_count > 0 || result.budget_exhausted
    ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
    : "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100";
  return `<div role="status" class="rounded-lg border px-3 py-2.5 text-[12px] ${tone}">
      <div class="font-semibold">Cleanup ${esc(result.status)} · reclaimed ${esc(formatBytes(result.observed_reclaimed_bytes))}</div>
      <div class="mt-1">Removed ${esc(String(result.removed_count))} · skipped ${esc(String(result.skipped_count))} · already absent ${esc(String(result.already_absent_count))} · failed ${esc(String(result.failed_count))}${result.budget_exhausted ? " · budget exhausted" : ""}</div>
    </div>
    <div class="flex justify-end gap-2">
      <button type="button" data-rescan class="${theme.button}">Scan again</button>
    </div>`;
}

export function openCleanup(remote: string, onCleaned: (remote: string) => void): void {
  const { body } = createDialog(`Clean up storage · ${remoteLabel(remote)}`);
  let currentTier: CleanupTier = "safe";
  let currentPlan: CleanupPlanResult | null = null;
  let busy = false;
  let requestSeq = 0;

  const wireCommon = (): void => {
    body.querySelectorAll<HTMLButtonElement>("[data-tier]").forEach(btn => {
      btn.addEventListener("click", () => {
        const tier = btn.dataset.tier as CleanupTier;
        if (busy || !tier || tier === currentTier) return;
        currentTier = tier;
        void loadPlan();
      });
    });
    const rescan = body.querySelector<HTMLButtonElement>("[data-rescan]");
    if (rescan) rescan.addEventListener("click", () => { if (!busy) void loadPlan(); });
    const apply = body.querySelector<HTMLButtonElement>("[data-apply]");
    if (apply) apply.addEventListener("click", () => { if (!busy && currentPlan) void runApply(currentPlan); });
  };

  const loadPlan = async (): Promise<void> => {
    const seq = ++requestSeq;
    busy = true;
    currentPlan = null;
    body.innerHTML = `${tierTabs(currentTier, true)}<p role="status" class="text-[12px] ${theme.quiet}">Scanning eligible storage for the ${esc(currentTier)} tier…</p>`;
    const result = await postRemoteCleanup({ action: "plan", remote, tier: currentTier });
    if (!body.isConnected || seq !== requestSeq) return;
    busy = false;
    if (!result.ok) {
      body.innerHTML = `${tierTabs(currentTier, false)}${renderFailure(result)}<div class="flex justify-end"><button type="button" data-rescan class="${theme.button}">Retry scan</button></div>`;
      wireCommon();
      return;
    }
    currentPlan = result;
    body.innerHTML = `${tierTabs(currentTier, false)}${renderPlanView(result, false)}`;
    wireCommon();
  };

  const runApply = async (plan: CleanupPlanResult): Promise<void> => {
    const seq = ++requestSeq;
    busy = true;
    body.innerHTML = `${tierTabs(currentTier, true)}${renderPlanView(plan, true)}`;
    const result = await postRemoteCleanup({
      action: "apply",
      remote,
      tier: plan.tier,
      plan_id: plan.plan_id,
      confirm: true,
    });
    if (!body.isConnected || seq !== requestSeq) return;
    busy = false;
    currentPlan = null;
    if (!result.ok) {
      toast(`Cleanup failed: ${result.message}`, "err");
      body.innerHTML = `${tierTabs(currentTier, false)}${renderFailure(result)}<div class="flex justify-end"><button type="button" data-rescan class="${theme.button}">Re-scan</button></div>`;
      wireCommon();
      return;
    }
    toast(`Reclaimed ${formatBytes(result.observed_reclaimed_bytes)} (${result.removed_count} removed)`, "ok");
    body.innerHTML = `${tierTabs(currentTier, false)}${renderApplyResult(result)}`;
    wireCommon();
    onCleaned(remote);
  };

  void loadPlan();
}
