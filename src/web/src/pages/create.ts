import { $ } from "../dom";
import { currentRoute, hostContext, localHostPath, navigate } from "../router";
import { act } from "../actions";
import { toast } from "../ui/toast";

// Public entry kept for the existing call sites (sidebar "New" button + the
// welcome CTA) — both just route to this page.
export function doCreate(): void {
  if (hostContext(currentRoute()).kind !== "local") {
    toast("New instance is available only for the local host", "err");
    return;
  }
  navigate("/create");
}

async function chooseProjectDirectory(): Promise<void> {
  const selected = await window.sandboxDesktop?.chooseProjectDirectory();
  if (selected) ($("createProject") as HTMLInputElement).value = selected;
}

export function initCreateView(): void {
  const button = document.getElementById("chooseProject");
  if (!button || !window.sandboxDesktop) return;
  button.hidden = false;
  button.addEventListener("click", () => void chooseProjectDirectory());
}

export function submitCreate(): void {
  const projectDir = ($("createProject") as HTMLInputElement).value.trim();
  const label = ($("createLabel") as HTMLInputElement).value.trim().toLowerCase();
  if (!projectDir.startsWith("/")) {
    toast("enter an absolute local project path", "err");
    return;
  }
  if (label && !/^[a-z0-9][a-z0-9_-]{0,30}$/.test(label)) {
    toast("label must use a-z, 0-9, _ or -", "err");
    return;
  }
  void act(label || "new instance", "create", { project_dir: projectDir, label });
}

export function createView(): string {
  return `<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="${localHostPath()}" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Create an instance</h1>
    <p class="mt-2 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Create a local instance from an existing project directory. Each plugin repo carries its own
      <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">sandbox.config.json</code>,
      and the resolved directory becomes the instance identity.</p>

    <div class="mt-5 space-y-4 rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <div><label for="createProject" class="block text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">Local project directory</label>
      <p class="mt-1 text-[11px] text-neutral-500 dark:text-neutral-400">Existing absolute path on this machine. It is validated again by the local-only dashboard service.</p>
      <div class="mt-2 flex gap-2"><input id="createProject" autocomplete="off" placeholder="/Users/you/Sites/plugin" class="min-w-0 flex-1 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100">
      <button id="chooseProject" hidden type="button" class="rounded-lg border border-neutral-300 px-3 py-2 text-[13px] font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800">Choose folder</button></div></div>
      <div><label for="createLabel" class="block text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">Instance label <span class="font-normal text-neutral-400">optional</span></label>
      <input id="createLabel" autocomplete="off" maxlength="31" placeholder="review" class="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"></div>
      <div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Creates on the local host only. Remote creation is unavailable until remote lifecycle operations have a service-backed API.</div>
      <button onclick="sb.submitCreate()" class="rounded-lg bg-blue-700 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500">Create local instance</button>
    </div>

    <p class="mt-4 text-[12.5px] text-neutral-500 dark:text-neutral-400">Creation runs as a background job. Progress opens in the activity panel.</p>

    <div class="mt-7">
      <a href="${localHostPath()}" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`;
}
