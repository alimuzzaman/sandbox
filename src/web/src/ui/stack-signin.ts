import { esc } from "../dom";
import { postSignIn } from "../api";
import type { HostingStack } from "../types";
import { theme } from "../theme";
import { toast } from "./toast";

type StackSignInTarget = Pick<HostingStack, "remote" | "slug" | "page_access">;
// Only identities are retained, never one-time URLs. Polling may replace buttons.
const pendingSignIns = new Set<string>();
const signInKey = (remote: string, stack: string): string => JSON.stringify([remote, stack]);

export function stackSignInAction(stack: StackSignInTarget): string {
  const status = stack.page_access?.sign_in;
  const quiet = theme.quiet;
  if (!status?.available) {
    const reason = status?.message || "Sign-in availability is unknown. Refresh this stack page on its controller.";
    const reasonId = `stack-signin-${stack.remote}-${stack.slug}`;
    return `<div class="mt-2 text-left sm:text-right"><button type="button" disabled aria-describedby="${esc(reasonId)}" class="cursor-not-allowed text-[13px] ${quiet}" title="${esc(reason)}">WordPress admin</button><p id="${esc(reasonId)}" class="mt-0.5 text-[11px] ${quiet}">${esc(reason)}</p></div>`;
  }
  const pending = pendingSignIns.has(signInKey(stack.remote, stack.slug));
  return `<div class="mt-2 text-left sm:text-right"><button type="button" data-signin data-remote="${esc(stack.remote)}" data-stack="${esc(stack.slug)}" ${pending ? "disabled" : ""} onclick="sb.signInStack(this)" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">${pending ? "Opening WordPress admin…" : "WordPress admin"}</button><p role="status" class="mt-0.5 text-[11px] ${quiet}">${pending ? "Waiting for the one-time sign-in response." : "Opens a fresh one-time login."}</p></div>`;
}

export async function signInStack(button: HTMLButtonElement): Promise<void> {
  const remote = button.dataset.remote, stack = button.dataset.stack;
  if (!remote || !stack || button.disabled) return;
  const key = signInKey(remote, stack);
  if (pendingSignIns.has(key)) { toast('WordPress sign-in is already pending for this stack.', 'info'); return; }
  const tab = window.open('about:blank', '_blank');
  if (!tab) { toast('Allow popups to open WordPress admin.', 'err'); return; }
  tab.opener = null;
  pendingSignIns.add(key);
  button.disabled = true;
  button.textContent = 'Opening WordPress admin…';
  try {
    const result = await postSignIn({ remote, stack });
    if (!result.ok) throw new Error(result.message);
    const url = new URL(result.url);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Invalid sign-in response.');
    tab.location.replace(url.href);
  } catch (error) {
    tab.close();
    toast(error instanceof Error ? error.message : 'Sign-in unavailable. Check this stack\'s WordPress autologin setup.', 'err');
  } finally {
    pendingSignIns.delete(key);
    button.disabled = false;
    button.textContent = 'WordPress admin';
    // Update only still-available matching controls, without replacing focused DOM.
    document.querySelectorAll<HTMLButtonElement>('[data-signin]').forEach(current => {
      if (current.dataset.remote !== remote || current.dataset.stack !== stack) return;
      current.disabled = false;
      current.textContent = 'WordPress admin';
      if (current.nextElementSibling) current.nextElementSibling.textContent = 'Opens a fresh one-time login.';
    });
  }
}
