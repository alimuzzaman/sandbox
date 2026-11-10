// Stacks and hosted sites (spec 073): every server a stack created, with its
// state, health and ref, plus plain hosted targets. Read-only: nothing here
// posts an action.

import { esc } from "../dom";
import { store } from "../state";
import { theme } from "../theme";
import type { HostingApply, HostingHealth, HostingServer, HostingStack, HostingTarget } from "../types";

const when = (epoch: number | null | undefined): string =>
  epoch ? new Date(epoch * 1000).toLocaleString() : "unknown";
const short = (commit: string | null | undefined): string => commit ? commit.slice(0, 12) : "unknown";

const HEALTH: Record<HostingHealth["status"], [string, string]> = {
  checking: ["checking…", "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"],
  up: ["up", "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"],
  up_login: ["up (login required)", "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"],
  redirect: ["redirects", "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200"],
  client_error: ["client error", "bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-100"],
  server_error: ["server error", "bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200"],
  unreachable: ["unreachable", "bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200"],
  not_checked: ["not checked", "bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400"],
};

const STATE: Record<HostingServer["state"], string> = {
  serving: "serving", redirecting: "redirecting", applying: "applying", failed: "last apply failed",
  instance_not_found: "instance not found", unknown: "unknown",
};

const ORIGIN: Record<HostingServer["origin"], string> = {
  route: "route", linked: "WordPress · linked", matched_by_name: "WordPress · matched by name",
};

const APPLY_CLASS: Record<string, string> = {
  succeeded: "text-emerald-700 dark:text-emerald-300",
  failed: "text-rose-700 dark:text-rose-300",
  applying: "text-amber-700 dark:text-amber-300",
};

function badge(text: string, cls: string, title = ""): string {
  return `<span ${title ? `title="${esc(title)}"` : ""} class="inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-medium ${cls}">${esc(text)}</span>`;
}

function healthBadge(health: HostingHealth): string {
  const [label, cls] = HEALTH[health.status] || HEALTH.not_checked;
  const detail = health.http_status ? `HTTP ${health.http_status}` : "";
  const checked = health.checked_at ? `checked ${when(health.checked_at)}` : "";
  return badge(detail && health.status !== "up" ? `${label} · ${detail}` : label, cls,
    [detail, checked].filter(Boolean).join(" · "));
}

function hostCell(row: HostingServer): string {
  const name = row.url
    ? `<a href="${esc(row.url)}" target="_blank" rel="noopener noreferrer" class="font-medium text-blue-700 hover:underline dark:text-blue-300">${esc(row.hostname)}</a>`
    : `<span class="font-medium">${esc(row.hostname)}</span> ${badge("wildcard", "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300")}`;
  const notes = [row.primary ? "primary" : "", row.mode === "redirect" && row.target ? `redirects to ${row.target}` : "",
    row.instance ? `instance ${row.instance}` : ""].filter(Boolean).join(" · ");
  return `${name}${notes ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">${esc(notes)}</div>` : ""}`;
}

function refCell(row: HostingServer): string {
  if (!row.ref.branch && !row.ref.commit) return `<span class="${theme.quiet}">unknown</span>`;
  return `<span title="${esc(row.ref.commit || "")}">${row.ref.branch ? `${esc(row.ref.branch)} · ` : ""}<code class="text-[11px]">${esc(short(row.ref.commit))}</code></span>`;
}

function serversTable(rows: HostingServer[], empty: string): string {
  const body = rows.length ? rows.map(row => `<tr class="border-t border-neutral-200 align-top dark:border-neutral-800">
      <td class="p-3 text-[12px]">${hostCell(row)}</td>
      <td class="p-3 text-[12px] ${theme.muted}">${esc(ORIGIN[row.origin])}</td>
      <td class="p-3 text-[12px]">${esc(STATE[row.state] || row.state)}</td>
      <td class="p-3 text-[12px]">${healthBadge(row.health)}</td>
      <td class="p-3 text-[12px]">${refCell(row)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="p-4 text-[12px] ${theme.quiet}">${esc(empty)}</td></tr>`;
  return `<div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${["Server", "Kind", "State", "Health", "Built from"]
    .map(label => `<th scope="col" class="px-3 pb-2 pt-1 ${theme.label}">${label}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function applyLine(apply: HostingApply | null): string {
  if (!apply) return `<span class="${theme.quiet}">not recorded</span>`;
  const time = apply.finished_at || apply.started_at;
  return `<span class="font-semibold ${APPLY_CLASS[apply.status] || ""}">${esc(apply.status)}</span>
    <span class="${theme.quiet}"> · ${esc(when(time))}</span>${apply.request_id
      ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">request <code>${esc(apply.request_id)}</code></div>` : ""}`;
}

function field(label: string, value: string): string {
  return `<div class="min-w-0"><dt class="${theme.label}">${esc(label)}</dt><dd class="mt-1 break-words text-[12px] text-neutral-800 dark:text-neutral-100">${value}</dd></div>`;
}

function sourceNote(source: string, remote: string): string {
  return source === "this-server" ? `this server · recorded as ${remote}` : `controller record · ${remote}`;
}

function stackCard(stack: HostingStack): string {
  const otherPorts = Object.entries(stack.ports).filter(([name]) => name !== "mailpit")
    .map(([name, port]) => `${name} ${port}`).join(", ");
  return `<section class="${theme.panel} overflow-hidden" aria-labelledby="stack-${esc(stack.key)}">
    <header class="flex flex-col gap-2 border-b border-neutral-200 px-5 py-4 dark:border-neutral-800 sm:flex-row sm:items-start sm:justify-between">
      <div class="min-w-0"><div class="${theme.label}">Stack</div>
        <h2 id="stack-${esc(stack.key)}" class="mt-1 truncate text-[18px] font-semibold">${esc(stack.slug)}</h2>
        <p class="mt-0.5 text-[12px] ${theme.quiet}">${esc(stack.project)} · ${esc(stack.environment)} · ${esc(sourceNote(stack.source, stack.remote))}</p></div>
      <div class="text-[12px] sm:text-right">${applyLine(stack.last_apply)}</div>
    </header>
    <div class="px-2 pb-2 pt-3">${serversTable(stack.servers, "No servers recorded for this stack yet.")}</div>
    <dl class="grid gap-4 border-t border-neutral-200 bg-neutral-50 px-5 py-4 dark:border-neutral-800 dark:bg-neutral-950 sm:grid-cols-2 lg:grid-cols-4">
      ${field("Ref", `${esc(stack.ref_branch || stack.ref || "detached")}<div class="mt-0.5"><code class="text-[11px]" title="${esc(stack.commit)}">${esc(stack.commit || "unknown")}</code></div>`)}
      ${field("Template", stack.template ? `<code class="text-[11px]">${esc(stack.template)}</code>` : `<span class="${theme.quiet}">unknown</span>`)}
      ${field("Mailpit", stack.mailpit_port ? `<code>127.0.0.1:${esc(stack.mailpit_port)}</code><div class="mt-0.5 text-[11px] ${theme.quiet}">loopback only, on the server</div>` : `<span class="${theme.quiet}">no mailpit port</span>`)}
      ${field("Created", `${esc(when(stack.created_at))}${stack.ttl_expires_at ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">expires ${esc(when(stack.ttl_expires_at))}</div>` : ""}`)}
      ${otherPorts || stack.loopback_port ? field("Ports", `${stack.loopback_port ? `app <code>127.0.0.1:${esc(stack.loopback_port)}</code>` : ""}${otherPorts ? `<div class="mt-0.5">${esc(otherPorts)}</div>` : ""}`) : ""}
    </dl>
  </section>`;
}

function targetRow(target: HostingTarget): string {
  const note = target.recorded ? "" : `<p class="px-5 pb-4 text-[12px] ${theme.quiet}">Applied before this panel recorded details; its hostnames appear after its next <code>sb host apply</code>.</p>`;
  return `<section class="${theme.panel} overflow-hidden">
    <header class="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
      <div class="min-w-0"><div class="${theme.label}">Hosted site</div>
        <h3 class="mt-1 truncate text-[15px] font-semibold">${esc(target.project)} <span class="font-normal ${theme.quiet}">· ${esc(target.environment)}</span></h3>
        <p class="mt-0.5 text-[12px] ${theme.quiet}">${esc(sourceNote(target.source, target.remote))}${target.commit ? ` · <code title="${esc(target.commit)}">${esc(short(target.commit))}</code>` : ""}</p></div>
      <div class="text-[12px] sm:text-right">${applyLine(target.last_apply)}</div>
    </header>
    ${target.recorded ? `<div class="px-2 pb-2">${serversTable(target.servers, "No hostnames recorded.")}</div>` : note}
  </section>`;
}

export function hostingView(): string {
  const data = store.hosting;
  const status = store.hostingError ? store.hostingError
    : data ? `Read-only · updated ${new Date(data.generated_at * 1000).toLocaleTimeString()}` : "Loading…";
  const header = `<header class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div class="${theme.label}">Hosting</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">Stacks and hosted sites</h1>
      <p class="mt-1 text-[13px] ${theme.muted}">Every server created for each stack, with its state, health and the ref it was built from.</p></div>
      <div class="flex items-center gap-3"><div role="status" class="text-[12px] ${store.hostingError ? "text-red-700 dark:text-red-300" : theme.quiet}">${esc(status)}</div>
      <button class="${theme.button}" onclick="sb.refreshHosting()">Refresh</button></div></header>`;
  if (!data) return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${header}</div></div>`;
  const partial = data.partial_reasons.length ? `<div role="status" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Some records could not be read: ${esc(data.partial_reasons.join(", "))}. Everything else is shown.</div>` : "";
  const empty = !data.stacks.length && !data.targets.length
    ? `<div class="${theme.panel} p-8 text-center text-[13px] ${theme.quiet}">No stacks or hosted sites yet. Create one with <code>sb host stack up</code> or <code>sb host apply</code>.</div>` : "";
  const stacks = data.stacks.length ? `<section class="space-y-4"><div class="flex items-center justify-between"><h2 class="text-[14px] font-semibold">Stacks</h2><span class="text-[11px] ${theme.quiet}">${data.stacks.length} stack${data.stacks.length === 1 ? "" : "s"}</span></div>${data.stacks.map(stackCard).join("")}</section>` : "";
  const targets = data.targets.length ? `<section class="space-y-4"><div class="flex items-center justify-between"><h2 class="text-[14px] font-semibold">Hosted sites</h2><span class="text-[11px] ${theme.quiet}">projects hosted with <code>sb host apply</code>, without a stack</span></div>${data.targets.map(targetRow).join("")}</section>` : "";
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${header}${partial}${empty}${stacks}${targets}</div></div>`;
}

export function hostingStillChecking(): boolean {
  const data = store.hosting;
  if (!data) return false;
  return [...data.stacks, ...data.targets].some(item => item.servers.some(row => row.health.status === "checking"));
}
