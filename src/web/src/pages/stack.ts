// One stack and its direct shared stacks, using the existing hosting poller.
import { esc } from "../dom";
import { store } from "../state";
import { theme } from "../theme";
import { hostingPath, stackPath } from "../router";
import { stackCard } from "./hosting";
import type { HostingStack } from "../types";
export { signInStack } from "../ui/stack-signin";

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
        `<a data-link href="${esc(stackPath(remote, name))}" class="text-[13px] text-blue-700 hover:underline dark:text-blue-300">Open shared stack</a>${stackCard(used)}` :
        `<p class="text-[13px] ${theme.quiet}">Shared stack ${esc(name)} is missing from this controller's records.</p>`}</section>`;
    }).join("");
    content = `${stackCard(stack)}${shared}`;
  }
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${head}${content}</div></div>`;
}
