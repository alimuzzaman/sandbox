// Right-side console drawer: streams job output (activity mode) and hosts the
// interactive per-instance terminal. Also owns pollJob (job streaming).

import { $ } from "../dom";
import { store } from "../state";
import { postAction, fetchJob } from "../api";
import { toast } from "./toast";
import { jobFlags, startPoll } from "./poller";

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
const flags = jobFlags(store);

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
  const owner = {};
  drawerOwner = owner;
  const release = flags.begin(instance);
  consoleOpen(label || "Working…");
  let titled = !!label;
  startPoll((offset) => fetchJob(id, offset), 800, {
    onSnapshot(j) {
      if (drawerOwner !== owner) return;
      if (!titled && j.status) { $("conTitle").textContent = j.status.replace(/ [✓✗]$/, ""); titled = true; }
      if (j.chunk) consoleWrite(j.chunk);
    },
    onDone(j) {
      release();
      repaint();
      if (drawerOwner === owner) consoleStatus(j.status || "done", j.ok);
      toast(j.status || "done", j.ok ? "ok" : "err");
      void refresh();
    },
    onLost() {
      release();
      repaint();
      const message = "Lost contact with this job; it may still be running";
      if (drawerOwner === owner) consoleStatus(message, false);
      toast(message, "err");
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
  drawerOwner = TERMINAL;
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
  if (!r.job_id) { consoleWrite((r.output || "failed") + "\n"); termBusy = false; $("conDot").className = "w-2 h-2 rounded-full bg-red-500"; return; }
  const ownsDrawer = (): boolean => drawerOwner === TERMINAL;
  const finish = (color: string): void => {
    termBusy = false;
    if (ownsDrawer()) $("conDot").className = "w-2 h-2 rounded-full " + color;
  };
  startPoll((offset) => fetchJob(r.job_id!, offset), 500, {
    onSnapshot(j) {
      if (j.chunk && ownsDrawer()) consoleWrite(j.chunk);
    },
    onDone() {
      finish("bg-emerald-500");
      if (!ownsDrawer()) return;
      consoleWrite("\n");
      input.focus();
    },
    onLost() {
      finish("bg-red-500");
      if (ownsDrawer()) consoleWrite("\nLost contact with this command; it may still be running\n");
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
