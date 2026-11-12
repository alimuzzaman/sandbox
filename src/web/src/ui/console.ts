// Right-side console drawer: streams job output (activity mode) and hosts the
// interactive per-instance terminal. Also owns pollJob (job streaming).

import { $, esc } from "../dom";
import { store } from "../state";
import { fetchJob } from "../api";
import { toast } from "./toast";
import { busyHolder, jobFlags, startPoll } from "./poller";
import { setConsoleExpanded } from "./console-access";

// main.ts injects refresh() to avoid a circular import.
let refresh: () => Promise<void> = async () => {};
export function setConsoleRefresh(fn: () => Promise<void>): void { refresh = fn; }
// refresh() does nothing while another job still pauses it, so a job that ends then
// repaints directly; otherwise its busy spinner stays until the last job ends.
let repaint: () => void = () => {};
export function setConsoleRepaint(fn: () => void): void { repaint = fn; }

// One owner writes to the drawer. A job that loses it keeps polling silently, so its
// completion and flags still resolve.
const TERMINAL = {};
let drawerOwner: object | null = null;
let opener: HTMLElement | null = null;
const observing = new Map<string, object>();

export function activityView(): string {
  const rows = Object.entries(store.operations);
  if (!rows.length) return "";
  return `<section aria-label="Local action activity" class="mx-4 mt-4 rounded border border-brd p-3 dark:border-neutral-700 text-[12px]">
    <h2 class="font-semibold">Local action activity</h2><p>Receipts are valid only while the same dashboard service is running. After a restart, use supported instance observation instead.</p>
    ${rows.map(([key, op]) => `<div class="mt-2 break-words"><span>${esc(op.label)}: ${esc(op.phase)}</span>
      ${op.jobId ? `<span> · Job ${esc(op.jobId)}</span><button type="button" data-observe-key="${esc(key)}" class="ml-2 underline">${op.phase === "observing" ? "Show activity" : "Observe original job"}</button>` : ""}
      ${op.phase === "unconfirmed" ? '<p role="status">No receipt was confirmed. This action remains guarded. The API has no request lookup; inspect local instances through supported observation before any manual reconciliation. Refreshing this page loses the guard and does not prove completion.</p>' : ""}
      ${op.phase === "lost" ? '<p role="status">Observation stopped; the job may still be running. Observe reads the same receipt without submitting again. Only use it while the same dashboard service remains running; receipts are not durable across a service restart.</p>' : ""}
      ${op.phase === "succeeded" ? '<p>Server reported completion. Refresh local instances to inspect the result; no instance identity is inferred from the label.</p>' : ""}
    </div>`).join("")}</section>`;
}

const flags = jobFlags(store);

export function consoleOpen(title: string): void {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  setConsoleExpanded($("console"), true);
  $("conTitle").textContent = title;
  $("conBody").textContent = "";
  $("conInputRow").classList.add("hidden");
  $("conDot").className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
  if (!$("console").inert) $("conClose").focus();
}

export function consoleClose(): void {
  setConsoleExpanded($("console"), false);
  if (opener?.isConnected) opener.focus();
  $("conInputRow").classList.add("hidden");
}

function consoleWrite(t: string): void {
  const b = $("conBody");
  b.textContent += t;
  b.scrollTop = b.scrollHeight;
}

function consoleStatus(s: string, ok: boolean | null | undefined): void {
  $("conTitle").textContent = s;
  $("conDot").className = "w-2 h-2 rounded-full " + (ok ? "bg-emerald-500" : "bg-red-500");
}

// Inline output stays only in the drawer, never in the operation metadata.
export function showActionResult(label: string, output: string, ok: boolean): void {
  drawerOwner = {};
  consoleOpen(label);
  consoleWrite(output || "The server returned no output.\n");
  consoleStatus(label + (ok ? ": completed" : ": failed"), ok);
}

export function pollJob(id: string, instance: string | null, label?: string): void {
  const key = instance ?? id;
  if (observing.has(key)) {
    const owner = observing.get(key)!;
    if (drawerOwner !== owner) {
      consoleOpen(label || "Activity");
      consoleWrite(`Job ${id}: following new output. Earlier output can be read by observing again after this observation stops.\n`);
      drawerOwner = owner;
    } else {
      setConsoleExpanded($("console"), true);
      if (!$("console").inert) $("conClose").focus();
    }
    return;
  }
  const owner = {};
  drawerOwner = owner;
  observing.set(key, owner);
  // Pause refresh only during this bounded observation, never release a
  // mutation guard merely because contact was lost.
  const release = flags.begin(null);
  consoleOpen(label || "Working…");
  consoleWrite(`Original job ${id}. Reading from its first output.\n`);
  let previousOffset = 0;
  startPoll(async (offset) => {
    const j = await fetchJob(id, offset);
    if (!j || typeof j !== "object" || j.job_id !== id || typeof j.done !== "boolean" ||
        typeof j.status !== "string" || typeof j.chunk !== "string" ||
        !Number.isSafeInteger(j.offset) || j.offset! < previousOffset ||
        (j.done ? typeof j.ok !== "boolean" : j.ok !== null)) throw new Error("Invalid job snapshot");
    previousOffset = j.offset!;
    return j;
  }, 800, {
    onSnapshot(j) {
      if (drawerOwner !== owner) return;
      if (j.chunk) consoleWrite(j.chunk);
    },
    onDone(j) {
      release(); observing.delete(key);
      if (instance) {
        delete store.busy[instance];
        store.operations[instance] = { label: label || "Action", jobId: id, phase: j.ok ? "succeeded" : "failed" };
      }
      repaint();
      if (drawerOwner === owner) consoleStatus(j.status || "Completed", j.ok);
      toast(j.status || "Completed", j.ok ? "ok" : "err");
      void refresh();
    },
    onLost() {
      release(); observing.delete(key);
      if (instance) {
        store.busy[instance] = "job outcome unconfirmed";
        store.operations[instance] = { label: label || "Action", jobId: id, phase: "lost" };
      }
      repaint();
      const message = "Observation stopped; completion is unconfirmed";
      if (drawerOwner === owner) {
        consoleStatus(message, false);
        consoleWrite("\nUse Local action activity to observe this same job again. No action has been resubmitted.\n");
      }
      toast(message, "err");
      void refresh();
    },
  });
}

// ---- interactive terminal ----
let termInstance: string | null = null;
const termHist: string[] = [];
let termHistIdx = -1;

export function openTerminal(name: string): void {
  consoleOpen("Terminal · " + name);
  termInstance = name;
  drawerOwner = TERMINAL;
  $("conTitle").textContent = "Terminal — " + name;
  $("conDot").className = "w-2 h-2 rounded-full bg-emerald-500";
  if (!$("conBody").textContent!.trim())
    consoleWrite("Terminal for " + name + " — runs inside the container.\n" +
      "Try: wp plugin list · wp option get siteurl · ls wp-content/plugins\n\n");
  $("conInputRow").classList.remove("hidden");
  setTimeout(() => { if (!$("console").inert) ($("conInput") as HTMLInputElement).focus(); }, 60);
}

async function runTerm(): Promise<void> {
  const input = $("conInput") as HTMLInputElement;
  const cmd = input.value.trim();
  if (!cmd || !termInstance) return;
  termHist.push(cmd); termHistIdx = termHist.length;
  input.value = "";
  // Terminal commands can mutate too: use the same stable instance guard.
  window.sb.op(termInstance, "term", { cmd });
}

function termKey(e: KeyboardEvent): void {
  const input = $("conInput") as HTMLInputElement;
  if (e.key === "Enter") { e.preventDefault(); runTerm(); }
  else if (e.key === "ArrowUp") {
    if (termHistIdx > 0) { termHistIdx--; input.value = termHist[termHistIdx] || ""; }
    e.preventDefault();
  } else if (e.key === "ArrowDown") {
    if (termHistIdx < termHist.length - 1) { termHistIdx++; input.value = termHist[termHistIdx] || ""; }
    else { termHistIdx = termHist.length; input.value = ""; }
    e.preventDefault();
  }
}

export function initConsole(): void {
  setConsoleExpanded($("console"), false);
  $("console").addEventListener("keydown", event => {
    if (event.key === "Escape") consoleClose();
  });
  document.addEventListener("click", event => {
    const button = (event.target as Element).closest<HTMLElement>("[data-observe-key]");
    if (!button) return;
    const key = button.dataset.observeKey!;
    const operation = store.operations[key];
    if (!operation?.jobId || observing.has(key)) {
      if (operation?.jobId) pollJob(operation.jobId, key, operation.label);
      return;
    }
    // An explicit read-only re-observation, never postAction.
    if (operation.phase !== "lost" && busyHolder(store.busy, key) !== null) {
      toast("Another action is pending. Wait before reading this completed receipt.", "info");
      return;
    }
    store.busy[key] = "observing original job";
    operation.phase = "observing";
    pollJob(operation.jobId, key, operation.label);
    repaint();
  });
  ($("conClose") as HTMLButtonElement).onclick = consoleClose;
  $("conInput").addEventListener("keydown", (e) => termKey(e as KeyboardEvent));
}
