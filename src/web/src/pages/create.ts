// "Create an instance" route (/create). Sandbox is per-project now: instances
// are created from the CLI (cd into a plugin repo, `./sb init`), so the
// dashboard no longer has a create form — this page points the user there.

import { navigate } from "../router";

// Public entry kept for the existing call sites (sidebar "New" button + the
// welcome CTA) — both just route to this page.
export function doCreate(): void {
  navigate("/create");
}

export function createView(): string {
  return `<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="/" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Create an instance</h1>
    <p class="mt-2 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Sandbox is per-project: each plugin repo carries its own
      <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">sandbox.config.json</code>,
      and instances are created from the CLI — one per project directory — not from the dashboard.</p>

    <div class="mt-5 rounded border border-brdin dark:border-neutral-700 p-4 bg-app dark:bg-neutral-900">
      <p class="text-[12.5px] text-neutral-600 dark:text-neutral-400 mb-2">In a plugin repo:</p>
      <pre class="text-[13px] leading-relaxed text-neutral-800 dark:text-neutral-200"><code>cd &lt;plugin-repo&gt;
./sb init     # scaffold config, boot an instance, provision the test harness
./sb test     # run its phpunit tests</code></pre>
    </div>

    <p class="mt-4 text-[12.5px] text-neutral-400">The instance appears here once it boots (this list refreshes automatically).</p>

    <div class="mt-7">
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`;
}
