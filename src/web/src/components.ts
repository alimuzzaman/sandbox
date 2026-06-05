// Small reusable HTML-string render helpers shared by the page views.

import { esc } from "./dom";

export function spinner(color?: string): string {
  return `<svg class="spin w-3.5 h-3.5 ${color === "white" ? "text-white" : "text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`;
}

export function statusPill(running: boolean): string {
  const base = "inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";
  return running
    ? `<span class="${base} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`
    : `<span class="${base} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`;
}

export function sectionHead(title: string, hint?: string): string {
  return `<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${title}</div>
    ${hint ? `<div class="text-[12px] text-neutral-400">${hint}</div>` : ""}</div>`;
}

export const row = (label: string, val: string): string =>
  `<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${label}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${val}</div></div>`;

export function pillLink(href: string, label: string, enabled: boolean): string {
  const base = "px-4 py-1.5 rounded-full border text-[13px]";
  if (enabled)
    return `<a href="${href}" target="_blank" class="${base} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${label}</a>`;
  return `<span title="Start the site first" class="${base} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${label}</span>`;
}

export const toolBtn =
  "px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";

export function opBtn(name: string, action: string, label: string,
                      extra?: Record<string, unknown>): string {
  return `<button class="${toolBtn}" onclick='sb.op(${JSON.stringify(name)},${JSON.stringify(action)},${JSON.stringify(extra || {})})'>${label}</button>`;
}

export function snippet(text: string): string {
  return `<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${esc(text)}</code>
    <button onclick='sb.copyText(${JSON.stringify(text)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`;
}

export function infoCard(title: string, body: string): string {
  return `<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${title}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${body}</div></div>`;
}

export const fmtN = (n?: number): string => (n || 0).toLocaleString();
export const fmt$ = (n?: number): string =>
  "$" + (n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
