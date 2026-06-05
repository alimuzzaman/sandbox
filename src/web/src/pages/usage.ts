import { esc } from "../dom";
import { store } from "../state";
import { sectionHead, fmtN, fmt$ } from "../components";
import type { TokenBucket } from "../types";

const tkSum = (u: TokenBucket): number =>
  (u.in || 0) + (u.out || 0) + (u.cw || 0) + (u.cr || 0);

export function usageView(): string {
  const u = store.usage;
  if (!u || !u.available)
    return `<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;
  const t = u.total || {};
  const statCard = (label: string, val: string, sub?: string): string =>
    `<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${label}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${val}</div>
      ${sub ? `<div class="text-[12px] text-neutral-400 mt-0.5">${sub}</div>` : ""}</div>`;
  const modelRows = Object.entries(u.by_model || {}).map(([m, v]) => `
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${m}</span>
      <span class="flex-1 text-neutral-500">${fmtN(tkSum(v))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${fmt$(v.cost)}</span></div>`).join("");
  const instRows = Object.entries(u.per_instance || {})
    .sort((a, b) => (b[1].cost || 0) - (a[1].cost || 0)).map(([i, v]) => `
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${i === "unattributed" ? "text-neutral-400 italic" : "text-neutral-700 dark:text-neutral-300"}">${esc(i)}</span>
      <span class="flex-1 text-neutral-500">${fmtN(tkSum(v))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${fmt$(v.cost)}</span></div>`).join("");
  const sessions = u.sessions || [];
  const sess = sessions.map((s) => `
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${s.id}</code>
      <span class="w-16 capitalize text-neutral-500">${s.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(s.instances || []).join(", ") || "—"}</span>
      <span class="text-neutral-500">${fmtN(s.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${fmt$(s.cost)}</span></div>`).join("");
  return `<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Claude usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Across all sandbox Claude sessions. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${statCard("Total tokens", fmtN(u.tokens))}
      ${statCard("Estimated cost", fmt$(u.cost))}
      ${statCard("Sessions", fmtN(sessions.length) + (sessions.length >= 25 ? "+" : ""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${fmtN(t.in)} · out ${fmtN(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${fmtN(t.cw)} · cache read ${fmtN(t.cr)}</div>
    </div>
    <div class="mt-6">${sectionHead("By model")}${modelRows || '<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${sectionHead("By instance", "Best-effort — attributed by which mcp__sandbox-… tools each session used")}${instRows || '<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${sectionHead("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${sess}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`;
}

export const usageTokenSum = tkSum;
