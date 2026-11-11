// One stack and its direct shared stacks, using the existing hosting poller.
import { esc } from "../dom";
import { store } from "../state";
import { theme } from "../theme";
import { hostingPath, stackPath } from "../router";
import { stackCard } from "./hosting";
import type { HostingStack } from "../types";
import { postSignIn } from "../api";
import { toast } from "../ui/toast";

function signInAction(stack: HostingStack): string {
  const status = stack.page_access?.sign_in;
  if (!status?.available) return `<p class="text-[13px] ${theme.quiet}">${esc(status?.message || 'Sign-in availability is unknown. Refresh this stack page on its controller.')}</p>`;
  return `<button type="button" data-remote="${esc(stack.remote)}" data-stack="${esc(stack.slug)}" onclick="sb.signInStack(this)" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">Sign in to WordPress admin</button>`;
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

function findStack(remote: string, slug: string): HostingStack | undefined {
  return store.hosting?.stacks.find(stack => stack.slug === slug &&
    (stack.source === "this-server" ? remote === "this-server" : stack.remote === remote));
}

export function stackView(remote: string, slug: string): string {
  const stack = findStack(remote, slug);
  const back = `<a data-link href="${hostingPath()}" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">All stacks and hosted sites</a>`;
  const head = `<header>${back}<h1 class="mt-3 text-[26px] font-semibold tracking-tight">Stack ${esc(slug)}</h1><p class="mt-1 text-[13px] ${theme.quiet}">${esc(remote)}</p></header>`;
  let content: string;
  if (!store.hosting) content = `<p role="status">${esc(store.hostingError || "Loading stack…")}</p>`;
  else if (!stack) content = `<p role="status">No stack named ${esc(slug)} is recorded here.</p>`;
  else {
    const shared = (stack.page_access?.uses || []).map(name => {
      const used = findStack(remote, name);
      const title = `<h2 class="text-[16px] font-semibold">Shared · ${esc(name)}</h2>`;
      return `<section class="space-y-3">${title}${used ?
        `<a data-link href="${esc(stackPath(remote, name))}" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">Open shared stack</a>${signInAction(used)}${stackCard(used)}` :
        `<p class="text-[13px] ${theme.quiet}">Shared stack ${esc(name)} is missing from this controller's records.</p>`}</section>`;
    }).join("");
    content = `${signInAction(stack)}${stackCard(stack)}${shared}`;
  }
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${head}${content}</div></div>`;
}
