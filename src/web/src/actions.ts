// User actions: wrap /api/action calls, manage optimistic busy state, and
// route long-running ones into the console via pollJob.

import { $, esc, cap } from "./dom";
import { store } from "./state";
import { postAction, fetchSnapshots, fetchUsage } from "./api";
import { toast } from "./ui/toast";
import { modal } from "./ui/modal";
import { pollJob } from "./ui/console";

// main.ts injects these to avoid circular imports.
let refresh: () => Promise<void> = async () => {};
let render: () => void = () => {};
export function setActionDeps(deps: { refresh: () => Promise<void>; render: () => void }): void {
  refresh = deps.refresh; render = deps.render;
}

const ACTION_LABELS: Record<string, (n: string) => string> = {
  create: (n) => "Creating " + n,
  delete: (n) => "Deleting " + n,
  "start-all": () => "Starting all sites",
  "stop-all": () => "Stopping all sites",
};

export async function act(instance: string, action: string,
                          extra: Record<string, unknown> = {}): Promise<void> {
  store.busy[instance] = action;
  render();
  let r;
  try {
    r = await postAction(Object.assign({ instance, action }, extra));
  } catch (e) {
    delete store.busy[instance]; toast("request failed: " + e, "err"); render(); return;
  }
  if (r.job_id) {
    const lbl = ACTION_LABELS[action] ? ACTION_LABELS[action](instance) : cap(action) + " " + instance;
    toast(action.replace("-", " ") + " started…", "info");
    pollJob(r.job_id, instance, lbl);
  } else {
    delete store.busy[instance];
    if (r.ok) toast(cap(action) + " " + instance + " ✓", "ok");
    else toast((r.output || "failed").split("\n")[0], "err");
    await refresh();
  }
}

// Ops/terminal actions: always stream into the console panel.
export async function op(name: string, action: string,
                         extra: Record<string, unknown> = {}): Promise<void> {
  let r;
  try { r = await postAction(Object.assign({ instance: name, action }, extra)); }
  catch (e) { toast("request failed: " + e, "err"); return; }
  if (r.job_id) {
    const titles: Record<string, string> = {
      logs: "Logs", status: "Status", doctor: "Doctor", update: "Updating plugins",
      snapshot: "Snapshot", restore: "Restoring", seed: "Importing content", xdebug: "Xdebug",
      install: "Installing " + (extra.slug || "plugin"), wp: "wp " + (extra.args || ""),
    };
    pollJob(r.job_id, null, (titles[action] || cap(action)) + " — " + name);
  } else {
    toast((r.output || "failed").split("\n")[0], "err");
  }
}

export async function doFocus(name: string, slug: string): Promise<void> {
  if (slug === "") act(name, "unfocus");
  else if (slug) act(name, "focus", { slug });
}

// Switch the instance's web server in place. Backgrounded + streamed (it
// recreates the web tier and may pull the OpenLiteSpeed image). No-op if the
// picked server is the one already running.
export async function doServer(name: string, server: string): Promise<void> {
  const cur = store.data.instances.find((i) => i.name === name);
  if (!server || (cur && cur.server === server)) return;
  store.busy[name] = "server";
  render();
  let r;
  try { r = await postAction({ instance: name, action: "server", server }); }
  catch (e) { delete store.busy[name]; render(); toast("request failed: " + e, "err"); return; }
  if (r.job_id) {
    toast("switching " + name + " → " + server + "…", "info");
    pollJob(r.job_id, name, "Switching " + name + " → " + server);
  } else {
    delete store.busy[name]; render();
    toast((r.output || "failed").split("\n")[0], "err");
  }
}

export async function doDelete(name: string): Promise<void> {
  const v = await modal({
    title: "Delete " + name, danger: true, okText: "Delete",
    desc: "Stops + removes the stack, DB volume, and files. Type the name to confirm.",
    fields: [{ key: "confirm", placeholder: name }],
  });
  if (v && v.confirm === name) act(name, "delete", { confirm: name });
  else if (v) toast("name did not match — not deleted", "err");
}

export function doWp(name: string): void {
  const el = $("wpArgs") as HTMLInputElement;
  const args = el.value.trim();
  if (!args) { toast("enter a wp-cli command", "err"); return; }
  op(name, "wp", { args });
}

export async function doSnapshot(name: string): Promise<void> {
  const v = await modal({
    title: "Snapshot " + name, okText: "Save",
    desc: "Save the current DB + uploads under this name.",
    fields: [{ key: "name", placeholder: "snapshot name" }],
  });
  if (v && v.name) op(name, "snapshot", { name: v.name });
}

export async function doRestore(name: string): Promise<void> {
  let snaps: string[] = [];
  try { snaps = (await fetchSnapshots(name)).snapshots || []; } catch { /* ignore */ }
  if (!snaps.length) { toast("no snapshots for " + name, "err"); return; }
  const v = await modal({
    title: "Restore " + name, danger: true, okText: "Restore",
    desc: "Overwrites the current DB + uploads with the chosen snapshot.",
    fields: [{ key: "name", type: "select", options: snaps }],
  });
  if (v && v.name) op(name, "restore", { name: v.name });
}

export async function doSeed(name: string): Promise<void> {
  const seeds = store.data.seeds || [];
  if (!seeds.length) { toast("no WXR files in runtime/seeds/", "err"); return; }
  const v = await modal({
    title: "Seed " + name, okText: "Import",
    desc: "Import a WXR content file into this instance.",
    fields: [{ key: "file", type: "select", options: seeds }],
  });
  if (v && v.file) op(name, "seed", { file: v.file });
}

export function doInstall(name: string): void {
  const slug = (($("plugQ") as HTMLInputElement).value || "").trim()
    .toLowerCase().replace(/\s+/g, "-");
  if (!slug) { toast("type a plugin slug to install", "err"); return; }
  op(name, "install", { slug });
}

// Plugin search: filter curated local plugins live.
export function plugFilter(selected: string | null): void {
  const q = (($("plugQ") as HTMLInputElement).value || "").toLowerCase().trim();
  const box = $("plugResults");
  if (!q) { box.innerHTML = ""; return; }
  const cur = store.data.instances.find((i) => i.name === selected);
  const matches = (store.data.plugins || []).filter((s) => s.toLowerCase().includes(q)).slice(0, 8);
  box.innerHTML = matches.map((s) => `
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${esc(s)}</span>
      ${cur && cur.focus === s ? '<span class="text-[11px] text-emerald-500">focused</span>'
        : `<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${cur ? cur.name : ""}','${esc(s)}')">Symlink + focus</button>`}
    </div>`).join("")
    || `<div class="text-[12px] text-neutral-400 px-1">No local source matches “${esc(q)}”.
        Press Install to fetch it from WordPress.org.</div>`;
}

export function copyText(t: string, btn: HTMLElement): void {
  navigator.clipboard.writeText(t).then(() => {
    const o = btn.textContent;
    btn.textContent = "copied";
    setTimeout(() => { btn.textContent = o; }, 1200);
  });
}

export async function loadUsageThenRender(): Promise<void> {
  try { store.usage = await fetchUsage(); }
  catch { store.usage = { available: false }; }
  render();
}
