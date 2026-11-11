// "Share this stack" and "Live links" on stack cards (spec 081 follow-up, US6).
//
// A minted link is a login bypass. It lives only in the dialog built for it,
// which sits outside the re-rendered app root, so polling never touches it;
// closing the dialog removes the element and the only reference. Nothing here
// writes it to `store`, browser storage, the URL or the console.

import { esc } from "../dom";
import { theme } from "../theme";
import { modal } from "../ui/modal";
import { toast } from "../ui/toast";
import { postShare } from "../api";
import { canShare, refusalHelp, revocableId, shareTerms } from "../ui/sharelabels";
import type { ShareCreated, ShareList, ShareRefused } from "../types";

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const nowSeconds = (): number => Math.floor(Date.now() / 1000);
const when = (epoch: number): string => new Date(epoch * 1000).toLocaleString();

let openDialog: HTMLElement | null = null;

function closeDialog(): void {
  openDialog?.remove();
  openDialog = null;
}

/** One dialog at a time, on `document.body` below the confirm modal (z-40 < z-50). */
function dialog(title: string): HTMLElement {
  closeDialog();
  const el = document.createElement("div");
  el.className = "fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-label", title);
  el.innerHTML = `<div class="flex max-h-[85vh] w-full max-w-xl flex-col gap-3 overflow-y-auto rounded-lg border border-brd bg-app p-5 shadow-xl dark:border-brd-dark dark:bg-card-dark">
    <div class="flex items-start justify-between gap-3"><h2 class="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50">${esc(title)}</h2>
    <button type="button" data-close class="${theme.button}">Close</button></div>
    <div data-body class="flex flex-col gap-3 text-[13px] text-neutral-700 dark:text-neutral-200"></div></div>`;
  el.addEventListener("click", (e) => {
    if (e.target === el || (e.target as HTMLElement).closest("[data-close]")) closeDialog();
  });
  el.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDialog(); });
  document.body.appendChild(el);
  openDialog = el;
  (el.querySelector("[data-close]") as HTMLElement).focus();
  return el.querySelector("[data-body]") as HTMLElement;
}

function stackOf(button: HTMLElement): { remote: string; stack: string } | null {
  const remote = button.dataset.shareRemote || "", stack = button.dataset.shareStack || "";
  return canShare(remote) && SLUG.test(stack) ? { remote, stack } : null;
}

function refusal(body: HTMLElement, result: ShareRefused, remote: string, stack: string): void {
  const help = refusalHelp(result.code, result.message, remote, stack);
  body.innerHTML = `<div role="alert" class="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-900 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-100">
      <div class="font-medium"><code>${esc(result.code)}</code></div><div class="mt-1">${esc(result.message)}</div></div>
    ${help ? `<p>${esc(help)}</p>` : ""}`;
}

function copyLink(input: HTMLInputElement, button: HTMLButtonElement, note: HTMLElement): void {
  const done = () => { button.textContent = "Copied"; setTimeout(() => { button.textContent = "Copy"; }, 1500); };
  const fallback = () => {
    input.focus();
    input.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch { copied = false; }
    if (copied) done();
    else note.textContent = "The link is selected: press Ctrl+C (or Cmd+C) to copy it.";
  };
  // The clipboard API needs a secure context (loopback or https); a plain
  // http://<tailnet IP> page falls back to selecting the field.
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(input.value).then(done, fallback);
  else fallback();
}

function showLink(body: HTMLElement, result: ShareCreated): void {
  const notes = (result.notes || []).map(note => `<li>${esc(note)}</li>`).join("");
  body.innerHTML = `<p>Anyone with this link can open the stack page and its sign-in links. It is shown
      <strong>once</strong> and saved nowhere: give it to the person directly.</p>
    <div class="flex gap-2"><input data-share-link readonly aria-label="Share link" spellcheck="false" autocomplete="off"
      class="min-w-0 flex-1 rounded border border-brdin bg-app px-3 py-1.5 font-mono text-[12px] dark:border-neutral-700 dark:bg-neutral-900">
      <button type="button" data-copy class="${theme.button}">Copy</button></div>
    <p data-copy-note class="text-[12px] ${theme.quiet}" role="status"></p>
    <dl class="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-[12px]">
      <dt class="${theme.quiet}">Terms</dt><dd>${esc(shareTerms(result.expires_at, result.uses, result.expires_clamped, nowSeconds()))}</dd>
      <dt class="${theme.quiet}">Expires</dt><dd>${esc(when(result.expires_at))}</dd>
      <dt class="${theme.quiet}">Uses</dt><dd>${result.uses == null ? "no limit" : esc(String(result.uses))}</dd>
      <dt class="${theme.quiet}">Link id</dt><dd><code>${esc(result.link_id)}</code> · revoke it from “Live links”</dd>
      <dt class="${theme.quiet}">Target</dt><dd>${esc(result.target)} · ${esc(result.hostname)}</dd></dl>
    ${notes ? `<ul class="list-disc pl-5 text-[12px] ${theme.quiet}">${notes}</ul>` : ""}`;
  // Set as a property, not an attribute, so the link is not in the markup.
  const input = body.querySelector("[data-share-link]") as HTMLInputElement;
  input.value = result.link;
  const button = body.querySelector("[data-copy]") as HTMLButtonElement;
  const note = body.querySelector("[data-copy-note]") as HTMLElement;
  button.addEventListener("click", () => copyLink(input, button, note));
  input.addEventListener("focus", () => input.select());
}

export async function shareStack(button: HTMLElement): Promise<void> {
  const target = stackOf(button);
  if (!target) return;
  const ok = await modal({
    title: `Share stack ${target.stack}?`, okText: "Make link",
    desc: "This makes a link to the stack's page and its sign-in links, usable without the tailnet " +
      "for 7 days with no use limit, unless you revoke it. The link is shown once.",
  });
  if (!ok) return;
  let body = dialog(`Share ${target.stack}`);
  body.innerHTML = `<p class="${theme.quiet}" role="status">Making the link…</p>`;
  const result = await postShare({ action: "create", ...target });
  // Closed while minting: show the result anyway, so a minted link is never lost unseen.
  if (!openDialog) body = dialog(`Share ${target.stack}`);
  if (result.ok) showLink(body, result);
  else refusal(body, result, target.remote, target.stack);
}

function linksTable(result: ShareList): string {
  const revoke = (id: string) => revocableId(id)
    ? `<button type="button" data-revoke="${esc(id)}" class="text-[12px] text-rose-700 hover:underline dark:text-rose-300">Revoke</button>` : "";
  const links = result.links.map(row => `<tr class="border-t border-neutral-200 dark:border-neutral-800">
      <td class="py-2 pr-2"><code class="text-[11px]">${esc(row.id)}</code><div class="text-[11px] ${theme.quiet}">${esc(row.kind)} · ${esc(row.scope)}</div></td>
      <td class="py-2 pr-2 text-[12px]">${esc(when(row.expires_at))}</td>
      <td class="py-2 pr-2 text-[12px] tabular-nums">${esc(String(row.used))} / ${row.uses == null ? "∞" : esc(String(row.uses))}</td>
      <td class="py-2 text-right">${revoke(row.id)}</td></tr>`).join("");
  const sessions = result.sessions.map(row => `<tr class="border-t border-neutral-200 dark:border-neutral-800">
      <td class="py-2 pr-2"><code class="text-[11px]">${esc(row.id)}</code><div class="text-[11px] ${theme.quiet}">from ${esc(row.link)}</div></td>
      <td class="py-2 pr-2 text-[12px]">${esc(when(row.expires_at))}</td><td></td>
      <td class="py-2 text-right">${revoke(row.id)}</td></tr>`).join("");
  const table = (head: string, rows: string, empty: string) => `<div><div class="${theme.label}">${head}</div>
    ${rows ? `<table class="mt-1 w-full text-left"><tbody>${rows}</tbody></table>` : `<p class="mt-1 text-[12px] ${theme.quiet}">${empty}</p>`}</div>`;
  return `<p class="text-[12px] ${theme.quiet}">${esc(result.target)} · entry ${esc(result.entry)}. Ids only: a link itself is never shown again.</p>
    ${table("Links", links, "No live links.")}${table("Browser sessions", sessions, "No open sessions.")}`;
}

async function loadLinks(body: HTMLElement, target: { remote: string; stack: string }): Promise<void> {
  body.innerHTML = `<p class="${theme.quiet}" role="status">Loading…</p>`;
  const result = await postShare({ action: "list", ...target });
  if (!body.isConnected) return;
  if (!result.ok) { refusal(body, result, target.remote, target.stack); return; }
  body.innerHTML = linksTable(result);
  body.querySelectorAll<HTMLButtonElement>("[data-revoke]").forEach(button => {
    button.addEventListener("click", () => void revoke(body, target, button.dataset.revoke || ""));
  });
}

async function revoke(body: HTMLElement, target: { remote: string; stack: string }, id: string): Promise<void> {
  if (!revocableId(id)) return;
  const ok = await modal({
    title: `Revoke ${id}?`, okText: "Revoke", danger: true,
    desc: id.startsWith("l-") ? "The link and every browser session it opened stop working now."
      : "This browser session stops working now. Its link stays live.",
  });
  if (!ok || !body.isConnected) return;
  const result = await postShare({ action: "revoke", ...target, id });
  if (result.ok) toast(`Revoked: ${result.revoked_links} link(s), ${result.revoked_sessions} session(s)`, "ok");
  else toast(`${result.code}: ${result.message}`, "err");
  if (body.isConnected) await loadLinks(body, target);
}

export function shareLinks(button: HTMLElement): void {
  const target = stackOf(button);
  if (!target) return;
  void loadLinks(dialog(`Live links · ${target.stack}`), target);
}

/** The two buttons for a stack card; a server's own record gets the CLI hint instead. */
export function shareButtons(remote: string, stack: string): string {
  if (!canShare(remote) || !SLUG.test(stack))
    return `<p class="mt-1.5 text-[11px] ${theme.quiet}">Share links are made on the controller: <code>sb host share create --stack ${esc(stack)} --remote &lt;R&gt;</code></p>`;
  const data = `data-share-remote="${esc(remote)}" data-share-stack="${esc(stack)}"`;
  return `<div class="mt-2 flex flex-wrap gap-2 sm:justify-end">
    <button type="button" class="${theme.button}" ${data} onclick="sb.shareStack(this)">Share this stack</button>
    <button type="button" class="${theme.button}" ${data} onclick="sb.shareLinks(this)">Live links</button></div>`;
}
