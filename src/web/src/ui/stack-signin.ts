import { esc } from "../dom";
import { postSignIn } from "../api";
import type { HostingStack } from "../types";
import { theme } from "../theme";
import { toast } from "./toast";

type StackSignInTarget = Pick<HostingStack, "remote" | "slug" | "page_access">;

export function stackSignInAction(stack: StackSignInTarget): string {
  const status = stack.page_access?.sign_in;
  const quiet = theme.quiet;
  if (!status?.available) {
    const reason = status?.message || "Sign-in availability is unknown. Refresh this stack page on its controller.";
    const reasonId = `stack-signin-${stack.remote}-${stack.slug}`;
    return `<div class="mt-2 text-left sm:text-right"><button type="button" disabled aria-describedby="${esc(reasonId)}" class="cursor-not-allowed text-[13px] ${quiet}" title="${esc(reason)}">WordPress admin</button><p id="${esc(reasonId)}" class="mt-0.5 text-[11px] ${quiet}">${esc(reason)}</p></div>`;
  }
  return `<div class="mt-2 text-left sm:text-right"><button type="button" data-remote="${esc(stack.remote)}" data-stack="${esc(stack.slug)}" onclick="sb.signInStack(this)" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">WordPress admin</button><p class="mt-0.5 text-[11px] ${quiet}">Opens a fresh one-time login.</p></div>`;
}

export async function signInStack(button: HTMLButtonElement): Promise<void> {
  const remote = button.dataset.remote, stack = button.dataset.stack;
  if (!remote || !stack || button.disabled) return;
  const tab = window.open('about:blank', '_blank');
  if (!tab) { toast('Allow popups to open WordPress admin.', 'err'); return; }
  tab.opener = null;
  button.disabled = true;
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
    button.disabled = false;
  }
}
