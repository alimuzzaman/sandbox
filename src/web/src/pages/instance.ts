import { esc } from "../dom";
import { store } from "../state";
import { csel } from "../ui/csel";
import {
  spinner, statusPill, sectionHead, row, pillLink, toolBtn, opBtn, snippet, fmtN, fmt$,
} from "../components";
import { usageTokenSum } from "./usage";
import { welcome } from "./welcome";
import type { Instance } from "../types";

// Domain row: shows the .tst domain + whether it's plain http or secured https.
// On plain http it surfaces how to upgrade — `./sb secure <name>` — because the
// CA-trust step needs an interactive sudo password and so can't run from a UI
// button (it would hang headless); we show the exact command to run instead.
function domainCell(r: Instance): string {
  const dom = r.domain || "";
  const secured = r.url.startsWith("https://");
  const isSb = dom.endsWith(".tst");
  if (secured) {
    return `<span class="text-neutral-700 dark:text-neutral-200">${esc(dom)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;
  }
  const tip = isSb
    ? `<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${snippet("./sb secure " + r.name)}
       </div>`
    : "";
  return `<span class="text-neutral-700 dark:text-neutral-200">${esc(dom)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${tip}`;
}

// Per-instance Claude usage summary (best-effort, cached).
function instUsageLine(name: string): string {
  const u = store.usage;
  if (!u || !u.available)
    return `<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;
  const v = u.per_instance?.[name];
  if (!v) return `<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>`;
  return `<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${fmtN(usageTokenSum(v))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${fmt$(v.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`;
}

export function instanceView(r: Instance | null): string {
  if (!r) return welcome();
  if (r.pending) return `<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${esc(r.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${spinner()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${esc(r.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;

  const b = store.busy[r.name];
  const link = "text-accent dark:text-blue-400 hover:underline";
  const m = r.mailpit_port;
  const primaryBtn = r.running
    ? `<button onclick="sb.act('${r.name}','stop')" ${b ? "disabled" : ""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${b === "stop" ? spinner() : '<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`
    : `<button onclick="sb.act('${r.name}','start')" ${b ? "disabled" : ""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${b === "start" ? spinner("white") : "▶"} Start site</button>`;

  return `
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${esc(r.name)}</h1>
        ${statusPill(r.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${r.url} · <code>${esc(r.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${r.login_url ? pillLink(r.login_url, "Login", r.running) : pillLink(r.url + "/wp-admin", "Admin", r.running)}
      ${pillLink(r.url, "View site", r.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${primaryBtn}
    <button onclick="sb.act('${r.name}','restart')" ${(!r.running || b) ? "disabled" : ""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${b === "restart" ? spinner() : ""}Restart</button>
    ${r.name === "main" ? "" : `<button onclick="sb.doDelete('${r.name}')" ${b ? "disabled" : ""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${sectionHead("Overview")}
    ${row("Web server", `<div class="flex items-center gap-2">
        ${csel("serverSel", (store.data.servers && store.data.servers.length
                 ? store.data.servers : ["apache", "nginx", "litespeed"]).map((s) => ({ v: s, label: s })),
               r.server, (v) => window.sb.doServer(r.name, v), !!b || !r.running)}
        ${b === "server" ? spinner() : ""}
        ${!r.running ? '<span class="text-[11px] text-neutral-400">start the site to switch</span>' : ""}</div>`)}
    ${r.domain ? row("Domain", domainCell(r)) : ""}
    ${row("Site address", r.running
        ? `<a href="${r.url}" target="_blank" class="${link}">${r.url}</a>`
        : `<span class="text-neutral-400">${r.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${row("Test inbox", r.running
        ? `<a href="http://localhost:${m}" target="_blank" class="${link}">Mailpit · :${m}</a>`
        : `<span class="text-neutral-400">Mailpit · :${m} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${row("Active project", r.project === "—" ? '<span class="text-neutral-400">none</span>' : esc(r.project))}
    ${row("Focus plugin", `<div class="flex items-center gap-2">
        ${csel("focusSel", [{ v: "", label: "— none —" }].concat(store.data.plugins.map((s) => ({ v: s, label: s }))),
               r.focus && r.focus !== "—" ? r.focus : "", (v) => window.sb.doFocus(r.name, v), !!b)}
        ${b === "focus" || b === "unfocus" ? spinner() : ""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${sectionHead("Tools", "Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${opBtn(r.name, "logs", "Logs")}
      ${opBtn(r.name, "status", "Status")}
      ${opBtn(r.name, "doctor", "Doctor")}
      ${opBtn(r.name, "update", "Update plugins")}
      <button class="${toolBtn}" onclick="sb.doSnapshot('${r.name}')">Snapshot</button>
      <button class="${toolBtn}" onclick="sb.doRestore('${r.name}')">Restore…</button>
      <button class="${toolBtn}" onclick="sb.doSeed('${r.name}')">Seed…</button>
      ${opBtn(r.name, "xdebug", "Xdebug", { state: "status" })}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${r.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${toolBtn}" onclick="sb.doWp('${r.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${sectionHead("Plugins", "Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${r.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${toolBtn}" onclick="sb.doInstall('${r.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${sectionHead("Use with Claude", "Connect a Claude session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Claude in chat (simplest):</div>
      ${snippet("focus " + (r.focus && r.focus !== "—" ? r.focus : "<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${snippet("mcp__" + r.mcp_server + "__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${r.url}</code>.</div>
      ${instUsageLine(r.name)}
    </div>
  </div>`;
}
