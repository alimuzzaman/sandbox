// "New instance" onboarding — a full page (route /create) with the name →
// domain auto-fill, server/seed selects, plugin checklist, and submit.

import { store } from "../state";
import { esc } from "../dom";
import { csel, nextCselId } from "../ui/csel";
import { sectionHead } from "../components";
import { act } from "../actions";
import { navigate, instancePath } from "../router";
import type { ChecklistOption } from "../types";

let domainTouched = false;

// Stable element ids for the form controls (read back on submit).
const ID = {
  name: "cr_name", domain: "cr_domain", server: "cr_server", seed: "cr_seed",
  title: "cr_title", theme: "cr_theme", debug: "cr_debug", plugins: "cr_plugins",
};

// `doCreate` is the public entry kept for every existing call site (sidebar
// "New" button, welcome CTA) — it now just routes to the create page.
export function doCreate(): void {
  domainTouched = false;
  navigate("/create");
}

export function syncDomainFromName(nameEl: HTMLInputElement): void {
  if (domainTouched) return;
  const dom = document.getElementById(ID.domain) as HTMLInputElement | null;
  if (!dom) return;
  const slug = (nameEl.value || "").trim().toLowerCase()
    .replace(/[^a-z0-9-]/g, "-").replace(/^-+|-+$/g, "");
  dom.value = slug ? slug + ".sb" : "";
}

export function domainEdited(): void { domainTouched = true; }

const inputCls =
  "w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app "
  + "dark:bg-neutral-900 text-[13px] focus:border-accent outline-none";

function labelled(label: string, control: string, hint?: string): string {
  return `<label class="block">
    <span class="block text-[12px] font-medium text-neutral-600 dark:text-neutral-400 mb-1">${esc(label)}</span>
    ${control}
    ${hint ? `<span class="block text-[11.5px] text-neutral-400 mt-1">${esc(hint)}</span>` : ""}
  </label>`;
}

export function createView(): string {
  // Hidden inputs carry the csel-selected values for read-back on submit.
  const serverId = nextCselId();
  const serverOpts = (store.data.servers || []).map((s) => ({ v: s, label: s }));
  const serverVal = store.data.servers[0] || "";
  const serverSel =
    `<input type="hidden" id="${ID.server}" value="${esc(serverVal)}">`
    + csel(serverId, serverOpts, serverVal, (v) => {
        (document.getElementById(ID.server) as HTMLInputElement).value = v;
      }, false, true);

  const seedId = nextCselId();
  const seedList = ["none", ...(store.data.seeds || [])];
  const seedOpts = seedList.map((s) => ({ v: s, label: s }));
  const seedSel =
    `<input type="hidden" id="${ID.seed}" value="none">`
    + csel(seedId, seedOpts, "none", (v) => {
        (document.getElementById(ID.seed) as HTMLInputElement).value = v;
      }, false, true);

  const projOpts: ChecklistOption[] = (store.data.projects || []).map((p) => ({
    value: p.name, label: p.name,
    desc: p.description || (p.plugins || []).join(", "),
  }));
  const pluginItems = projOpts.map((o) => `
    <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
      hover:bg-neutral-100 dark:hover:bg-neutral-800">
      <input type="checkbox" data-plugin value="${esc(o.value)}" class="accent-accent w-3.5 h-3.5 mt-0.5">
      <span class="flex-1 min-w-0">
        <span class="text-neutral-800 dark:text-neutral-200">${esc(o.label)}</span>
        ${o.desc ? `<span class="block text-[11.5px] text-neutral-400 truncate">${esc(o.desc)}</span>` : ""}
      </span></label>`).join("");
  const pluginList = `<div id="${ID.plugins}" class="flex flex-col gap-0.5 max-h-56 overflow-y-auto
    rounded border border-brdin dark:border-neutral-700 p-1">${pluginItems
    || `<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>`}</div>`;

  return `<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="/" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">New instance</h1>
    <p class="mt-1.5 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Name it, pick a web server, then optionally add plugins and demo content. It'll serve at a
      clean <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">http://&lt;name&gt;.sb</code>
      (no port). Want HTTPS? run <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">./sb secure &lt;name&gt;</code> after.</p>

    <div class="mt-6">
      ${sectionHead("Basics")}
      <div class="flex flex-col gap-3.5">
        ${labelled("Name", `<input id="${ID.name}" oninput="sb.syncDomainFromName(this)"
          placeholder="name (a-z, 0-9, -)" class="${inputCls}">`)}
        ${labelled("Web server", serverSel)}
        ${labelled("Domain", `<input id="${ID.domain}" oninput="sb.domainEdited()"
          placeholder="domain — defaults to <name>.sb" class="${inputCls}">`)}
      </div>
    </div>

    <div class="mt-7">
      ${sectionHead("Plugins", "optional")}
      ${pluginList}
    </div>

    <div class="mt-7">
      ${sectionHead("Content & options", "optional")}
      <div class="flex flex-col gap-3.5">
        ${labelled("Demo content", seedSel)}
        ${labelled("Site title", `<input id="${ID.title}"
          placeholder="defaults to “Sandbox <name>”" class="${inputCls}">`)}
        ${labelled("Theme", `<input id="${ID.theme}"
          placeholder="theme slug (optional, e.g. astra)" class="${inputCls}">`)}
        <label class="flex items-center gap-2 text-[13px] text-neutral-700 dark:text-neutral-300 cursor-pointer select-none">
          <input type="checkbox" id="${ID.debug}" class="accent-accent w-3.5 h-3.5"> Enable WP_DEBUG</label>
      </div>
    </div>

    <div class="mt-8 flex items-center gap-2">
      <button onclick="sb.submitCreate()" class="px-4 py-2 rounded-full bg-accent text-white text-[13px] font-medium hover:bg-blue-700">Create instance</button>
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Cancel</a>
    </div>
  </div>`;
}

function val(id: string): string {
  return ((document.getElementById(id) as HTMLInputElement | null)?.value || "").trim();
}

export function submitCreate(): void {
  const name = val(ID.name);
  if (!name) {
    (document.getElementById(ID.name) as HTMLInputElement | null)?.focus();
    return;
  }
  const server = val(ID.server);
  const domain = val(ID.domain).toLowerCase();
  const seedVal = val(ID.seed);
  const seed = seedVal && seedVal !== "none" ? seedVal : "";
  const plugins = [...document.querySelectorAll<HTMLInputElement>(
    `#${ID.plugins} input[data-plugin]:checked`)].map((c) => c.value);
  const wpDebug = (document.getElementById(ID.debug) as HTMLInputElement | null)?.checked || false;

  // Optimistic: show the new site in the sidebar immediately + navigate to it.
  if (!store.data.instances.find((i) => i.name === name)) {
    store.data.instances.push({
      name, running: false, pending: true, server,
      url: "", mcp_server: "sandbox-" + name, project: "—", focus: "—", domain,
      wordpress_port: "", mailpit_port: "",
    });
  }
  store.busy[name] = "create";
  navigate(instancePath(name));
  act(name, "create", {
    name, server, domain, plugins, seed,
    site_title: val(ID.title),
    theme: val(ID.theme),
    wp_debug: wpDebug,
  });
}
