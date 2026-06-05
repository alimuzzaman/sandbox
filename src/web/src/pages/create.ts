// "New instance" onboarding form (single scrolling modal) + the live domain
// auto-fill from the name.

import { store } from "../state";
import { modal } from "../ui/modal";
import { act } from "../actions";
import { navigate, instancePath } from "../router";
import type { ChecklistOption, ModalField } from "../types";

let domainTouched = false;

export function syncDomainFromName(nameEl: HTMLInputElement): void {
  if (domainTouched) return;
  const dom = document.querySelector<HTMLInputElement>('#mFields [data-field="domain"]');
  if (!dom) return;
  const slug = (nameEl.value || "").trim().toLowerCase()
    .replace(/[^a-z0-9-]/g, "-").replace(/^-+|-+$/g, "");
  dom.value = slug ? slug + ".sb" : "";
}

export function domainEdited(): void { domainTouched = true; }

export async function doCreate(): Promise<void> {
  domainTouched = false;
  const dready = store.data.domains_ready;
  const desc = dready
    ? "Name it, pick a web server, then optionally add plugins and demo content. The domain fills in from the name."
    : "Name it, pick a web server, then optionally add plugins and demo content. Tip: run `./sb domains setup` once for trusted no-port HTTPS.";

  const projOpts: ChecklistOption[] = (store.data.projects || []).map((p) => ({
    value: p.name, label: p.name,
    desc: p.description || (p.plugins || []).join(", "),
  }));
  const seedOpts = ["none", ...(store.data.seeds || [])];

  const fields: ModalField[] = [
    { type: "label", label: "Basics" },
    { key: "name", placeholder: "name (a-z, 0-9, -)", oninput: "sb.syncDomainFromName(this)" },
    { key: "server", type: "select", options: store.data.servers },
    { key: "domain", placeholder: "domain — defaults to <name>.sb", oninput: "sb.domainEdited()" },
    { type: "label", label: "Plugins (optional)" },
    { key: "plugins", type: "checklist", options: projOpts },
    { type: "label", label: "Content & options (optional)" },
    { key: "seed", type: "select", options: seedOpts },
    { key: "site_title", placeholder: "site title — defaults to “Sandbox <name>”" },
    { key: "theme", placeholder: "theme slug (optional, e.g. astra)" },
    { key: "wp_debug", type: "checkbox", label: "Enable WP_DEBUG" },
  ];

  const v = await modal({ title: "New instance", okText: "Create", desc, fields });
  if (!v || !v.name) return;
  const name = String(v.name).trim();
  const domain = String(v.domain || "").trim().toLowerCase();
  const seedVal = String(v.seed || "");
  const seed = seedVal && seedVal !== "none" ? seedVal : "";
  const plugins = (v.plugins as string[]) || [];

  // Optimistic: show the new site in the sidebar immediately + navigate to it.
  if (!store.data.instances.find((i) => i.name === name)) {
    store.data.instances.push({
      name, running: false, pending: true, server: String(v.server),
      url: "", mcp_server: "sandbox-" + name, project: "—", focus: "—", domain,
      wordpress_port: "", mailpit_port: "",
    });
  }
  store.busy[name] = "create";
  navigate(instancePath(name));
  act(name, "create", {
    name, server: v.server, domain, plugins, seed,
    site_title: String(v.site_title || "").trim(),
    theme: String(v.theme || "").trim(),
    wp_debug: !!v.wp_debug,
  });
}
