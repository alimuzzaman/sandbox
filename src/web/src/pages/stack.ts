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
  const head = `<header>${back}<h1 class="mt-3 break-words text-[26px] font-semibold tracking-tight">Stack ${esc(slug)}</h1><div class="mt-2 flex flex-wrap items-center justify-between gap-3"><p class="text-[13px] ${theme.quiet}">${esc(remote === "this-server" ? "This server" : remote)}</p><button type="button" class="${theme.button}" onclick="sb.refreshHosting()">Refresh stack</button></div></header>`;
  const warning = store.hostingError && store.hosting
    ? `<p role="alert" class="text-[13px] text-red-700 dark:text-red-300">Refresh failed. Showing the last loaded records. ${esc(store.hostingError)}</p>` : "";
  const partial = store.hosting?.partial_reasons.length
    ? `<p role="status" class="text-[13px] ${theme.muted}">Some records could not be read: ${esc(store.hosting.partial_reasons.join(", "))}. Stack details may be incomplete.</p>` : "";
  let content: string;
  if (!store.hosting) content = `<p role="status">${esc(store.hostingError || "Loading stack…")}</p>`;
  else if (!stack) content = `<p role="status">Stack ${esc(slug)} is not in the loaded records for this host. Refresh or return to all stacks to check its current location.</p>`;
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
  return `<div class="${theme.page}"><div class="${theme.shell} space-y-6">${head}${warning}${partial}${content}</div></div>`;
}
