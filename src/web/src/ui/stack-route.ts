// Which stack a /hosting/<remote>/<slug> URL names (pure; tested in node).
//
// The canonical segment of a stack this server records itself is
// `this-server`, whatever name the row carries in `remote`. `/api/hosting`
// gives that name (and a `key` of `<remote>/<slug>`, an identity rather than a
// path), so a link built from it names the server's own remote name. Accept
// that name too, but only when it points to exactly one such stack, and tell
// the caller the canonical segment to replace the URL with.
import type { HostingStack } from "../types";

type Routable = Pick<HostingStack, "slug" | "source" | "remote">;

export function stackSegment(stack: Routable): string {
  return stack.source === "this-server" ? "this-server" : stack.remote;
}

export function resolveStack<T extends Routable>(stacks: readonly T[], remote: string, slug: string):
    { stack: T; remote: string } | undefined {
  const named = stacks.filter(stack => stack.slug === slug);
  const exact = named.find(stack => stackSegment(stack) === remote);
  if (exact) return { stack: exact, remote };
  if (remote === "this-server") return undefined;
  const aliased = named.filter(stack => stack.source === "this-server" && stack.remote === remote);
  return aliased.length === 1 ? { stack: aliased[0], remote: "this-server" } : undefined;
}
