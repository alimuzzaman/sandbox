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
import type { ShareCreated, ShareList, ShareRefused, SharePartial, ShareAttempt, ShareGrant } from "../types";

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const nowSeconds = (): number => Math.floor(Date.now() / 1000);
const when = (epoch: number): string => new Date(epoch * 1000).toLocaleString();

// Open dialogs. A new dialog replaces the others, except one that shows a
// minted link: that stays until its owner closes it, so a link is never lost.
const openDialogs = new Set<HTMLElement>();
const dialogFocus = new WeakMap<HTMLElement, HTMLElement>();
// Target identity survives card replacement by the hosting poller. No URLs.
const pendingShares = new Set<string>();
const pendingRevokes = new Set<string>();
const targetKey = (target: { remote: string; stack: string }): string => JSON.stringify([target.remote, target.stack]);

function closeDialog(el: HTMLElement): void {
  el.remove();
  openDialogs.delete(el);
  const previous = dialogFocus.get(el);
  if (previous?.isConnected) previous.focus();
}

/** A dialog on `document.body`, below the confirm modal (z-40 < z-50); returns its body. */
function dialog(title: string): HTMLElement {
  for (const other of [...openDialogs]) if (!other.dataset.holdsLink) closeDialog(other);
  const el = document.createElement("div");
  if (document.activeElement instanceof HTMLElement) dialogFocus.set(el, document.activeElement);
  el.className = "fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-label", title);
  el.innerHTML = `<div class="flex max-h-[85vh] w-full max-w-xl flex-col gap-3 overflow-y-auto rounded-lg border border-brd bg-app p-5 shadow-xl dark:border-brd-dark dark:bg-card-dark">
    <div class="flex items-start justify-between gap-3"><h2 class="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50">${esc(title)}</h2>
    <button type="button" data-close class="${theme.button}">Close</button></div>
    <div data-body class="flex flex-col gap-3 text-[13px] text-neutral-700 dark:text-neutral-200"></div></div>`;
  el.addEventListener("click", (e) => {
    if (e.target === el || (e.target as HTMLElement).closest("[data-close]")) closeDialog(el);
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); closeDialog(el); }
    if (e.key === "Tab") {
      const controls = [...el.querySelectorAll<HTMLElement>("button,input,a[href],[tabindex]")]
        .filter(control => control.tabIndex >= 0 && !control.matches(":disabled") && control.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  });
  document.body.appendChild(el);
  openDialogs.add(el);
  (el.querySelector("[data-close]") as HTMLElement).focus();
  return el.querySelector("[data-body]") as HTMLElement;
}

function stackOf(button: HTMLElement): { remote: string; stack: string } | null {
  const remote = button.dataset.shareRemote || "", stack = button.dataset.shareStack || "";
  return canShare(remote) && SLUG.test(stack) ? { remote, stack } : null;
}

function refusal(body: HTMLElement, result: ShareRefused | SharePartial, remote: string, stack: string): void {
  const help = refusalHelp(result.code, result.message, remote, stack);
  body.innerHTML = `<div role="alert" class="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-900 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-100">
      <div class="font-medium"><code>${esc(result.code)}</code></div><div class="mt-1">${esc(result.message)}</div></div>
    ${help ? `<p>${esc(help)}</p>` : ""}
    ${"gaps" in result ? (result.gaps || []).map(gap => `<div class="rounded border p-2"><p>${esc(gap.stack)} · ${esc(gap.target)} ${esc(gap.hostname || "")} · ${esc(gap.piece)} · ${esc(gap.code)}</p><p>${esc(gap.fix)}</p></div>`).join("") : ""}
    ${"phase" in result ? cleanupDetails(result.attempt_id, result.still_live) : ""}`;
}

function cleanupDetails(attemptId: string | undefined, grants: ShareGrant[]): string {
  return `<div role="alert"><p>cleanup_pending · ${esc(attemptId || "Attempt identity unavailable")}</p>
    <p>Grants may remain live. Do not make another share until cleanup is confirmed.</p>
    ${grants.map(grant => `<p><code>${esc(grant.id)}</code> · ${esc(grant.target)} · entry ${esc(grant.entry)} · expires ${esc(when(grant.expires_at))} · ${esc(grant.status)} · ${esc(grant.reason_code)}</p>`).join("")}
    <p>Repeat revoke with the original parent link id from Live links. Remaining grant ids above are evidence; cleanup uses the original durable attempt.</p></div>`;
}

/** Pure decision shared by the dialog and its bounded node regression. */
export function shareCreationBlocked(result: ShareList): boolean {
  return !result.ok || result.attempts.some(attempt => !["active", "revoked", "expired"].includes(attempt.phase));
}

function attemptsTable(attempts: ShareAttempt[]): string {
  return attempts.map(attempt => `<div class="rounded border p-2"><p><code>${esc(attempt.attempt_id)}</code> · ${esc(attempt.phase)} · parent <code>${esc(attempt.link_id)}</code> · expires ${esc(when(attempt.expires_at))}</p>
    ${!["revoked", "expired"].includes(attempt.phase) ? `<button type="button" data-revoke="${esc(attempt.link_id)}" class="${theme.button}">Repeat revoke ${esc(attempt.link_id)}</button>` : ""}
    ${!["active", "revoked", "expired"].includes(attempt.phase) ? cleanupDetails(attempt.attempt_id, attempt.still_live || []) : ""}</div>`).join("");
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
  (body.closest("[role=dialog]") as HTMLElement).dataset.holdsLink = "1";
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
  const key = targetKey(target);
  if (pendingShares.has(key) || pendingRevokes.has(key)) { toast("A sharing operation is already pending for this stack.", "info"); return; }
  pendingShares.add(key);
  updateShareButtons(target);
  try {
    await createShare(target);
  } finally {
    pendingShares.delete(key);
    updateShareButtons(target);
  }
}

function updateShareButtons(target: { remote: string; stack: string }): void {
  const key = targetKey(target);
  const revoking = pendingRevokes.has(key);
  const pending = revoking || pendingShares.has(key);
  document.querySelectorAll<HTMLButtonElement>("[data-share-create], [data-share-live]").forEach(button => {
    if (button.dataset.shareRemote !== target.remote || button.dataset.shareStack !== target.stack) return;
    button.disabled = pending;
    if (button.hasAttribute("data-share-create"))
      button.textContent = revoking ? "Revocation pending…" : pending ? "Sharing pending…" : "Share this stack";
  });
}

async function createShare(target: { remote: string; stack: string }): Promise<void> {
  const checking = dialog(`Share ${target.stack} · ${target.remote}`);
  checking.innerHTML = `<p role="status">Checking live links before sharing…</p>`;
  const inventory = await postShare({ action: "list", ...target });
  // Closing the preflight cancels this UI flow, before any create request.
  if (!checking.isConnected) return;
  if (!("attempts" in inventory)) {
    refusal(checking, inventory, target.remote, target.stack);
    return;
  }
  if (shareCreationBlocked(inventory)) {
    renderLinks(checking, target, inventory);
    return;
  }
  closeDialog(checking.closest("[role=dialog]") as HTMLElement);
  const ok = await modal({
    title: `Share ${target.stack} on ${target.remote}?`, okText: "Make link",
    desc: "This makes a link to the stack's page and its sign-in links, usable without the tailnet " +
      "for 7 days with no use limit, unless you revoke it. The link is shown once.",
  });
  if (!ok) return;
  let body = dialog(`Share ${target.stack} · ${target.remote}`);
  body.innerHTML = `<p class="${theme.quiet}" role="status">Making the link…</p>`;
  const result = await postShare({ action: "create", ...target });
  // This dialog was closed or replaced while minting: show the result in a new one,
  // so a minted link is never lost unseen. Another dialog holding a link stays open.
  if (!body.isConnected) body = dialog(`Share ${target.stack} · ${target.remote}`);
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
    ${rows ? `<div class="overflow-x-auto"><table aria-label="${head}" class="mt-1 w-full text-left"><thead><tr>${["Id", "Expires", "Uses", "Action"].map(label => `<th scope="col" class="py-1 pr-2 text-[11px]">${label}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="mt-1 text-[12px] ${theme.quiet}">${empty}</p>`}</div>`;
  return `<p class="text-[12px] ${theme.quiet}">${esc(result.target || "Target unavailable")} · entry ${esc(result.entry || "unknown")}. Ids only: a link itself is never shown again.</p>
    ${!result.ok ? `<p role="alert">Live status is incomplete. Grants may remain live. New sharing is blocked until status and cleanup are confirmed.</p>` : ""}
    ${result.partial_reasons?.map(reason => `<p>${esc(reason)}</p>`).join("") || ""}
    ${attemptsTable(result.attempts)}
    ${table("Links", links, result.ok ? "No live links." : "Live links could not be confirmed.")}${table("Browser sessions", sessions, result.ok ? "No open sessions." : "Browser sessions could not be confirmed.")}`;
}

async function loadLinks(body: HTMLElement, target: { remote: string; stack: string }): Promise<void> {
  body.innerHTML = `<p class="${theme.quiet}" role="status">Loading…</p>`;
  const result = await postShare({ action: "list", ...target });
  if (!body.isConnected) return;
  if (!("attempts" in result)) { refusal(body, result, target.remote, target.stack); return; }
  renderLinks(body, target, result);
}

function renderLinks(body: HTMLElement, target: { remote: string; stack: string }, result: ShareList): void {
  body.innerHTML = linksTable(result);
  body.querySelectorAll<HTMLButtonElement>("[data-revoke]").forEach(button => {
    button.addEventListener("click", () => void revoke(body, target, button.dataset.revoke || ""));
  });
}

async function revoke(body: HTMLElement, target: { remote: string; stack: string }, id: string): Promise<void> {
  if (!revocableId(id)) return;
  const key = targetKey(target);
  if (pendingRevokes.has(key) || pendingShares.has(key)) { toast("A sharing operation is already pending for this stack.", "info"); return; }
  pendingRevokes.add(key);
  updateShareButtons(target);
  try {
    await confirmRevoke(body, target, id);
  } finally {
    pendingRevokes.delete(key);
    updateShareButtons(target);
  }
}

async function confirmRevoke(body: HTMLElement, target: { remote: string; stack: string }, id: string): Promise<void> {
  const ok = await modal({
    title: `Revoke ${id} on ${target.stack} · ${target.remote}?`, okText: "Revoke", danger: true,
    desc: id.startsWith("l-") ? "Request revocation of this link and its browser sessions. Access may remain live until cleanup is confirmed."
      : "Request revocation of this browser session. Its link stays live. Wait for the result before repeating the request.",
  });
  if (!ok || !body.isConnected) return;
  body.innerHTML = `<p role="status">Revoking ${esc(id)}… Wait for the result before repeating this request.</p>`;
  const result = await postShare({ action: "revoke", ...target, id });
  if (!body.isConnected) body = dialog(`Revocation result · ${target.stack} · ${target.remote}`);
  if (result.ok) toast(`Revoked: ${result.revoked_links} link(s), ${result.revoked_sessions} session(s)`, "ok");
  else {
    if (body.isConnected) {
      refusal(body, result, target.remote, target.stack);
      const retry = document.createElement("button");
      retry.className = theme.button;
      retry.textContent = `Repeat revoke ${id}`;
      retry.addEventListener("click", () => void revoke(body, target, id));
      body.appendChild(retry);
    }
    return;
  }
  if (body.isConnected) await loadLinks(body, target);
}

export function shareLinks(button: HTMLElement): void {
  const target = stackOf(button);
  if (!target) return;
  if (pendingRevokes.has(targetKey(target)) || pendingShares.has(targetKey(target))) {
    toast("A sharing operation is pending. Wait for its result before refreshing live links.", "info");
    return;
  }
  void loadLinks(dialog(`Live links · ${target.stack} · ${target.remote}`), target);
}

/** The two buttons for a stack card; a server's own record gets the CLI hint instead. */
export function shareButtons(remote: string, stack: string): string {
  if (!canShare(remote) || !SLUG.test(stack))
    return `<p class="mt-1.5 text-[11px] ${theme.quiet}">Share links are made on the controller: <code>sb host share create --stack ${esc(stack)} --remote &lt;R&gt;</code></p>`;
  const data = `data-share-remote="${esc(remote)}" data-share-stack="${esc(stack)}"`;
  const key = targetKey({ remote, stack });
  const revoking = pendingRevokes.has(key);
  const pending = revoking || pendingShares.has(key);
  return `<div class="mt-2 flex flex-wrap gap-2 sm:justify-end">
    <button type="button" data-share-create class="${theme.button}" ${data} ${pending ? "disabled" : ""} onclick="sb.shareStack(this)">${revoking ? "Revocation pending…" : pending ? "Sharing pending…" : "Share this stack"}</button>
    <button type="button" data-share-live class="${theme.button}" ${data} ${pending ? "disabled" : ""} onclick="sb.shareLinks(this)">Live links</button></div>`;
}
