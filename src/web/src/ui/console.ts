// Right-side console drawer: streams job output (activity mode) and hosts the
// interactive per-instance terminal. Also owns pollJob (job streaming).

import { $ } from "../dom";
import { store } from "../state";
import { postAction, fetchJob } from "../api";
import { toast } from "./toast";
import { startPoll, type PollHandle } from "./poller";

// main.ts injects refresh() to avoid a circular import.
let refresh: () => Promise<void> = async () => {};
export function setConsoleRefresh(fn: () => Promise<void>): void { refresh = fn; }

// One poll owns the drawer at a time.
let activePoll: PollHandle | null = null;
function stopActivePoll(): void {
  activePoll?.cancel();
  activePoll = null;
}

export function consoleOpen(title: string): void {
  $("console").classList.remove("w-0");
  $("console").classList.add("w-[26rem]");
  $("conTitle").textContent = title;
  $("conBody").textContent = "";
  $("conInputRow").classList.add("hidden");
  $("conDot").className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
}

export function consoleClose(): void {
  stopActivePoll();
  $("console").classList.add("w-0");
  $("console").classList.remove("w-[26rem]");
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

export function pollJob(id: string, instance: string | null, label?: string): void {
  stopActivePoll();
  store.paused = true;
  consoleOpen(label || "Working…");
  let titled = !!label;
  const release = (): void => {
    store.paused = false;
    if (instance) delete store.busy[instance];
  };
  activePoll = startPoll((offset) => fetchJob(id, offset), 800, {
    onSnapshot(j) {
      if (!titled && j.status) { $("conTitle").textContent = j.status.replace(/ [✓✗]$/, ""); titled = true; }
      if (j.chunk) consoleWrite(j.chunk);
    },
    onDone(j) {
      activePoll = null;
      release();
      consoleStatus(j.status || "done", j.ok);
      toast(j.status || "done", j.ok ? "ok" : "err");
      void refresh();
    },
    onLost() {
      activePoll = null;
      release();
      const message = "Lost contact with this job; it may still be running";
      consoleStatus(message, false);
      toast(message, "err");
      void refresh();
    },
    onCancel() {
      release();
      void refresh();
    },
  });
}

// ---- interactive terminal ----
let termInstance: string | null = null;
const termHist: string[] = [];
let termHistIdx = -1;
let termBusy = false;

export function openTerminal(name: string): void {
  termInstance = name;
  $("console").classList.remove("w-0");
  $("console").classList.add("w-[26rem]");
  $("conTitle").textContent = "Terminal — " + name;
  $("conDot").className = "w-2 h-2 rounded-full bg-emerald-500";
  if (!$("conBody").textContent!.trim())
    consoleWrite("Terminal for " + name + " — runs inside the container.\n" +
      "Try: wp plugin list · wp option get siteurl · ls wp-content/plugins\n\n");
  $("conInputRow").classList.remove("hidden");
  setTimeout(() => ($("conInput") as HTMLInputElement).focus(), 60);
}

async function runTerm(): Promise<void> {
  if (termBusy) return;
  const input = $("conInput") as HTMLInputElement;
  const cmd = input.value.trim();
  if (!cmd || !termInstance) return;
  termHist.push(cmd); termHistIdx = termHist.length;
  input.value = "";
  consoleWrite("› " + cmd + "\n");
  termBusy = true;
  $("conDot").className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
  let r;
  try { r = await postAction({ instance: termInstance, action: "term", cmd }); }
  catch (e) { consoleWrite("error: " + e + "\n"); termBusy = false; return; }
  if (!r.job_id) { consoleWrite((r.output || "failed") + "\n"); termBusy = false; return; }
  stopActivePoll();
  const finish = (color: string): void => {
    activePoll = null;
    termBusy = false;
    $("conDot").className = "w-2 h-2 rounded-full " + color;
  };
  activePoll = startPoll((offset) => fetchJob(r.job_id!, offset), 500, {
    onSnapshot(j) {
      if (j.chunk) consoleWrite(j.chunk);
    },
    onDone() {
      finish("bg-emerald-500");
      consoleWrite("\n");
      input.focus();
    },
    onLost() {
      finish("bg-red-500");
      consoleWrite("\nLost contact with this command; it may still be running\n");
    },
    onCancel() {
      termBusy = false;
    },
  });
}

function termKey(e: KeyboardEvent): void {
  const input = $("conInput") as HTMLInputElement;
  if (e.key === "Enter") { runTerm(); }
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
  ($("conClose") as HTMLButtonElement).onclick = consoleClose;
  $("conInput").addEventListener("keydown", (e) => termKey(e as KeyboardEvent));
}
