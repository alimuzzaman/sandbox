// Right-side console drawer: streams job output (activity mode) and hosts the
// interactive per-instance terminal. Also owns pollJob (job streaming).

import { $ } from "../dom";
import { store } from "../state";
import { postAction, fetchJob } from "../api";
import { toast } from "./toast";

// main.ts injects refresh() to avoid a circular import.
let refresh: () => Promise<void> = async () => {};
export function setConsoleRefresh(fn: () => Promise<void>): void { refresh = fn; }

export function consoleOpen(title: string): void {
  $("console").classList.remove("w-0");
  $("console").classList.add("w-[26rem]");
  $("conTitle").textContent = title;
  $("conBody").textContent = "";
  $("conInputRow").classList.add("hidden");
  $("conDot").className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
}

export function consoleClose(): void {
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
  store.paused = true;
  consoleOpen(label || "Working…");
  let offset = 0;
  let titled = !!label;
  const t = setInterval(async () => {
    const j = await fetchJob(id, offset);
    if (!titled && j.status) { $("conTitle").textContent = j.status.replace(/ [✓✗]$/, ""); titled = true; }
    if (j.chunk) { consoleWrite(j.chunk); offset = j.offset ?? offset; }
    else if (typeof j.offset === "number") { offset = j.offset; }
    if (j.done) {
      clearInterval(t);
      store.paused = false;
      if (instance) delete store.busy[instance];
      consoleStatus(j.status || "done", j.ok);
      toast(j.status || "done", j.ok ? "ok" : "err");
      await refresh();
    }
  }, 800);
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
  let offset = 0;
  const t = setInterval(async () => {
    const j = await fetchJob(r.job_id!, offset);
    if (j.chunk) { consoleWrite(j.chunk); offset = j.offset ?? offset; }
    else if (typeof j.offset === "number") offset = j.offset;
    if (j.done) {
      clearInterval(t); termBusy = false; consoleWrite("\n");
      $("conDot").className = "w-2 h-2 rounded-full bg-emerald-500";
      input.focus();
    }
  }, 500);
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
