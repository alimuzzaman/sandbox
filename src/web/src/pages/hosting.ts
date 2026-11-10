// Stacks and hosted sites (spec 076): every server a stack created, with its
// state, health and ref, plus plain hosted targets. Read-only: nothing here
// posts an action.

import { esc } from "../dom";
import { store } from "../state";
import { theme } from "../theme";
import { hostingAnchor, hostingTargetPath } from "../router";
import type { HostedTargets, HostingApply, HostingHealth, HostingServer, HostingStack, HostingTarget, LifetimeRow, LifetimeView } from "../types";
import {
  applyLabel, compareLifetimes, expiryTone, lifetimeLabel, reapLabel, timeLeft, CPU_LABEL, CPU_TITLE, type Tone,
} from "../ui/hostlabels";

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

const TONE_TEXT: Record<Tone, string> = {
  good: "text-emerald-700 dark:text-emerald-300",
  warn: "text-amber-700 dark:text-amber-300",
  bad: "text-rose-700 dark:text-rose-300",
  quiet: "text-neutral-500 dark:text-neutral-400",
};
const TONE_BADGE: Record<Tone, string> = {
  good: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  warn: "bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-100",
  bad: "bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  quiet: "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300",
};
const nowSeconds = (): number => Math.floor(Date.now() / 1000);

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
      <td class="break-words p-3 text-[12px]">${hostCell(row)}</td>
      <td class="p-3 text-[12px] ${theme.muted}">${esc(ORIGIN[row.origin])}</td>
      <td class="p-3 text-[12px]">${esc(STATE[row.state] || row.state)}</td>
      <td class="p-3 text-[12px]">${healthBadge(row.health)}</td>
      <td class="break-words p-3 text-[12px]">${refCell(row)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="p-4 text-[12px] ${theme.quiet}">${esc(empty)}</td></tr>`;
  return `<div class="overflow-x-auto"><table class="w-full min-w-[640px] table-fixed text-left"><colgroup><col class="w-[34%]"><col class="w-[17%]"><col class="w-[13%]"><col class="w-[14%]"><col class="w-[22%]"></colgroup><thead><tr>${["Server", "Kind", "State", "Health", "Built from"]
    .map(label => `<th scope="col" class="px-3 pb-2 pt-1 ${theme.label}">${label}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function applyLine(apply: HostingApply | null, commit: string | null = null): string {
  if (!apply) {
    const label = applyLabel(null, null, false);
    return `<span class="${TONE_TEXT[label.tone]}">${esc(label.text)}</span>${commit
      ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">commit <code title="${esc(commit)}">${esc(short(commit))}</code></div>` : ""}`;
  }
  const label = applyLabel(apply.status, apply.reason, true);
  const time = apply.finished_at || apply.started_at;
  return `<span class="font-semibold ${TONE_TEXT[label.tone]}">${esc(label.text)}</span>
    <span class="${theme.quiet}"> · ${esc(when(time))}</span>${apply.request_id
      ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">request <code>${esc(apply.request_id)}</code></div>` : ""}`;
}

// ---- lifetimes (spec 077, read-only) ----

function lifetimeBadge(view: LifetimeView | undefined): string {
  const tone: Tone = !view || view.classification !== "recorded" ? "quiet"
    : view.lifetime === "temporary" ? expiryTone(view, nowSeconds()) === "quiet" ? "warn" : expiryTone(view, nowSeconds())
    : "good";
  const title = view?.classification === "unclassified"
    ? (view.reason === "invalid" ? "The lifetime record could not be read or is invalid; server cleanup never removes it."
      : "No lifetime record; server cleanup never removes it.")
    : view?.classification === "unknown" ? "This source's lifetime records could not be read yet." : "";
  return badge(lifetimeLabel(view), TONE_BADGE[tone], title);
}

function expiryCell(view: LifetimeView | undefined): string {
  if (!view || view.classification !== "recorded" || view.expires_at == null)
    return `<span class="${theme.quiet}">—</span>`;
  const tone = expiryTone(view, nowSeconds());
  return `<span title="${esc(when(view.expires_at))}" class="${TONE_TEXT[tone]}">${esc(timeLeft(view.expires_at, nowSeconds()))}</span>
    <div class="mt-0.5 text-[11px] ${theme.quiet}">${esc(when(view.expires_at))}</div>`;
}

function reapCell(view: LifetimeView | undefined): string {
  const text = reapLabel(view);
  return text.startsWith("reapable") ? badge(text, TONE_BADGE.bad) : `<span class="${theme.quiet}">${esc(text)}</span>`;
}

function lifetimeLine(view: LifetimeView): string {
  const parts = [view.owner ? `owner ${view.owner}` : "", view.state && view.state !== "active" ? view.state : "",
    view.extended_count ? `extended ${view.extended_count}×` : ""].filter(Boolean).join(" · ");
  const left = view.classification === "recorded" && view.expires_at != null
    ? ` <span class="${TONE_TEXT[expiryTone(view, nowSeconds())]}">${esc(timeLeft(view.expires_at, nowSeconds()))}</span>` : "";
  return `<div class="mt-1.5 flex flex-wrap items-center gap-1.5 sm:justify-end">${lifetimeBadge(view)}${left}${
    view.reap_eligible && view.classification === "recorded" ? reapCell(view) : ""}</div>${parts
    ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">${esc(parts)}</div>` : ""}`;
}

function lifetimesSection(rows: LifetimeRow[]): string {
  if (!rows.length) return "";
  const body = rows.map(row => {
    const anchor = hostingAnchor(row.source === "this-server" ? "this-server" : row.remote, row.project, row.environment);
    const name = row.card
      ? `<a href="#${esc(encodeURIComponent(anchor))}" class="font-medium text-blue-700 hover:underline dark:text-blue-300">${esc(row.name)}</a>`
      : `<span class="font-medium">${esc(row.name)}</span>`;
    const sub = [row.ref_branch || "", row.source === "this-server" ? "" : row.remote, row.card ? "" : "no card"]
      .filter(Boolean).join(" · ");
    return `<tr class="border-t border-neutral-200 align-top dark:border-neutral-800">
      <td class="break-words p-3 text-[12px]">${name}${sub ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">${esc(sub)}</div>` : ""}</td>
      <td class="break-words p-3 text-[12px]">${row.owner ? esc(row.owner) : `<span class="${theme.quiet}">—</span>`}</td>
      <td class="p-3 text-[12px]">${lifetimeBadge(row)}</td>
      <td class="p-3 text-[12px]">${expiryCell(row)}</td>
      <td class="p-3 text-[12px]">${reapCell(row)}</td>
      <td class="p-3 text-[12px]">${row.state ? esc(row.state) : `<span class="${theme.quiet}">—</span>`}</td>
      <td class="p-3 text-[12px] tabular-nums">${row.classification === "recorded" ? esc(String(row.extended_count)) : `<span class="${theme.quiet}">—</span>`}</td></tr>`;
  }).join("");
  return `<section class="space-y-3"><div class="flex flex-wrap items-end justify-between gap-2"><h2 class="text-[14px] font-semibold">Lifetimes</h2>
    <span class="text-[11px] ${theme.quiet}">temporary copies nearest expiry first · read-only; cleanup runs on the server</span></div>
    <div class="overflow-x-auto ${theme.panel}"><table class="w-full min-w-[760px] text-left"><thead><tr>${
      ["Copy", "Owner", "Lifetime", "Expires", "Cleanup", "State", "Extended"]
        .map(label => `<th scope="col" class="p-3 ${theme.label}">${label}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div></section>`;
}

function field(label: string, value: string): string {
  return `<div class="min-w-0"><dt class="${theme.label}">${esc(label)}</dt><dd class="mt-1 break-words text-[12px] text-neutral-800 dark:text-neutral-100">${value}</dd></div>`;
}

function sourceNote(source: string, remote: string): string {
  if (source !== "this-server") return `controller record · ${remote}`;
  return remote === "this-server" ? "this server" : `this server · recorded as ${remote}`;
}

function cardAnchor(row: { source: string; remote: string; project: string; environment: string }): string {
  return hostingAnchor(row.source === "this-server" ? "this-server" : row.remote, row.project, row.environment);
}

const usageBytes = (value: number): string => {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let n = Math.max(0, value), unit = 0;
  while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
  return `${n.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
};

// Hosted targets on one host, with the containers attributed to each, for the
// host pages. `remote` is the inventory's name ("this-server" on a server).
// `hosted` is undefined when that host's inventory predates hosted targets.
export function hostedTargetsSection(remote: string, hosted: HostedTargets | undefined, loading = false): string {
  const head = `<div class="mb-3 flex items-center justify-between gap-3"><h2 class="text-[14px] font-semibold">Hosted apps (sb host apply / stacks)</h2><span class="text-[11px] ${theme.quiet}">separate from Sandbox dev instances · read-only</span></div>`;
  if (!hosted) {
    const text = loading ? "Loading this host's inventory…" : "This host's inventory does not report hosted targets yet (its control service needs an update).";
    return `<section>${head}<div class="${theme.panel} p-4 text-[12px] ${theme.quiet}">${esc(text)}</div></section>`;
  }
  const sorted = [...hosted.rows].sort((a, b) => compareLifetimes(a.lifetime, b.lifetime) || a.key.localeCompare(b.key));
  const rows = sorted.map(row => {
    const apply = applyLabel(row.last_apply_status, row.last_apply_reason, row.recorded);
    const owner = row.lifetime?.owner ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">${esc(row.lifetime.owner)}</div>` : "";
    return `<tr class="border-t border-neutral-200 align-top dark:border-neutral-800">
    <td class="p-3 text-[12px] font-medium"><a data-link class="text-blue-700 hover:underline dark:text-blue-300" href="${esc(hostingTargetPath(remote, row.project, row.environment))}">${esc(row.project)} <span class="font-normal ${theme.quiet}">· ${esc(row.environment)}</span></a></td>
    <td class="p-3 text-[12px]">${row.stack_slug ? esc(row.stack_slug) : `<span class="${theme.quiet}">—</span>`}</td>
    <td class="p-3 text-[12px]">${row.lifetime ? `${lifetimeBadge(row.lifetime)}${owner}` : `<span class="${theme.quiet}" title="This host's runtime predates lifetimes in its inventory.">not reported</span>`}</td>
    <td class="p-3 text-[12px]">${expiryCell(row.lifetime)}</td>
    <td class="p-3 text-[12px]">${row.running ? "running" : `<span class="${theme.quiet}">no running containers</span>`}</td>
    <td class="p-3 text-[12px] tabular-nums">${esc(String(row.container_count))}</td>
    <td class="p-3 text-[12px] tabular-nums">${esc(usageBytes(row.memory_used_bytes))}</td>
    <td class="p-3 text-[12px] tabular-nums" title="${esc(CPU_TITLE)}">${esc(String(row.cpu_percent))}%</td>
    <td class="p-3 text-[12px] ${TONE_TEXT[apply.tone]}">${esc(apply.text)}</td></tr>`;
  }).join("");
  const body = rows || `<tr><td colspan="9" class="p-4 text-[12px] ${theme.quiet}">No hosted targets on this host.</td></tr>`;
  const labels: Array<[string, string]> = [["Target", ""], ["Stack", ""], ["Lifetime", ""], ["Expires", ""], ["State", ""],
    ["Containers", ""], ["RAM", ""], [CPU_LABEL, CPU_TITLE], ["Last apply", ""]];
  return `<section>${head}<div class="overflow-x-auto ${theme.panel}"><table class="w-full min-w-[880px] text-left"><thead><tr>${labels.map(([label, title]) => `<th scope="col" ${title ? `title="${esc(title)}"` : ""} class="p-3 ${theme.label}">${esc(label)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div><p class="mt-2 text-[11px] ${theme.quiet}">${esc(CPU_TITLE)}</p></section>`;
}

function stackCard(stack: HostingStack): string {
  const otherPorts = Object.entries(stack.ports).filter(([name]) => name !== "mailpit")
    .map(([name, port]) => `${name} ${port}`).join(", ");
  return `<section id="${esc(cardAnchor(stack))}" class="${theme.panel} scroll-mt-4 overflow-hidden" aria-labelledby="stack-${esc(stack.key)}">
    <header class="flex flex-col gap-2 border-b border-neutral-200 px-5 py-4 dark:border-neutral-800 sm:flex-row sm:items-start sm:justify-between">
      <div class="min-w-0"><div class="${theme.label}">Stack</div>
        <h2 id="stack-${esc(stack.key)}" class="mt-1 truncate text-[18px] font-semibold">${esc(stack.slug)}</h2>
        <p class="mt-0.5 text-[12px] ${theme.quiet}">${esc(stack.project)} · ${esc(stack.environment)} · ${esc(sourceNote(stack.source, stack.remote))}</p></div>
      <div class="text-[12px] sm:text-right">${applyLine(stack.last_apply)}${lifetimeLine(stack.lifetime)}</div>
    </header>
    <div class="px-2 pb-2 pt-3">${serversTable(stack.servers, "No servers recorded for this stack yet.")}</div>
    <dl class="grid gap-4 border-t border-neutral-200 bg-neutral-50 px-5 py-4 dark:border-neutral-800 dark:bg-neutral-950 sm:grid-cols-2 lg:grid-cols-4">
      ${field("Ref", `${esc(stack.ref_branch || stack.ref || "detached")}<div class="mt-0.5"><code class="text-[11px]" title="${esc(stack.commit)}">${esc(short(stack.commit))}</code></div>`)}
      ${field("Template", stack.template ? `<code class="text-[11px]">${esc(stack.template)}</code>` : `<span class="${theme.quiet}">unknown</span>`)}
      ${field("Mailpit", stack.mailpit_port ? `<code>127.0.0.1:${esc(stack.mailpit_port)}</code><div class="mt-0.5 text-[11px] ${theme.quiet}">loopback only, on the server</div>` : `<span class="${theme.quiet}">no mailpit port</span>`)}
      ${field("Created", `${esc(when(stack.created_at))}${stack.ttl_expires_at ? `<div class="mt-0.5 text-[11px] ${theme.quiet}">expires ${esc(when(stack.ttl_expires_at))}</div>` : ""}`)}
      ${otherPorts || stack.loopback_port ? field("Ports", `${stack.loopback_port ? `app <code>127.0.0.1:${esc(stack.loopback_port)}</code>` : ""}${otherPorts ? `<div class="mt-0.5">${esc(otherPorts)}</div>` : ""}`) : ""}
    </dl>
  </section>`;
}

function targetRow(target: HostingTarget): string {
  const note = target.recorded ? "" : `<p class="px-5 pb-4 text-[12px] ${theme.quiet}">Applied before tracking: its hostnames and apply outcome appear after its next <code>sb host apply</code>.</p>`;
  return `<section id="${esc(cardAnchor(target))}" class="${theme.panel} scroll-mt-4 overflow-hidden">
    <header class="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
      <div class="min-w-0"><div class="${theme.label}">Hosted site</div>
        <h3 class="mt-1 truncate text-[15px] font-semibold">${esc(target.project)} <span class="font-normal ${theme.quiet}">· ${esc(target.environment)}</span></h3>
        <p class="mt-0.5 text-[12px] ${theme.quiet}">${esc(sourceNote(target.source, target.remote))}${target.stack_slug ? ` · stack <span class="font-medium">${esc(target.stack_slug)}</span>` : ""}${target.commit && target.recorded ? ` · <code title="${esc(target.commit)}">${esc(short(target.commit))}</code>` : ""}</p></div>
      <div class="text-[12px] sm:text-right">${applyLine(target.last_apply, target.recorded ? null : target.commit)}${lifetimeLine(target.lifetime)}</div>
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
  const lifetimes = lifetimesSection(data.lifetimes || []);
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${header}${partial}${empty}${lifetimes}${stacks}${targets}</div></div>`;
}

export function hostingStillChecking(): boolean {
  const data = store.hosting;
  if (!data) return false;
  return [...data.stacks, ...data.targets].some(item => item.servers.some(row => row.health.status === "checking"));
}
