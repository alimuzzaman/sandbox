// History-API router with clean URLs. The current view is a pure function of
// location.pathname — there is no separate `selected`/`viewMode` state.
//
//   /                        -> home (welcome, or auto-first instance)
//   /instance/<name>         -> instance detail
//   /instance/<name>/console -> instance detail + console drawer open
//   /host/local              -> local host overview
//   /usage                   -> agent usage page
//
// On hard-refresh/deep-link the Python server serves the app for any non-/api
// path (SPA fallback), then this router renders the right view.

export type Route =
  | { page: "home" }
  | { page: "create" }
  | { page: "local-host" }
  | { page: "instance"; name: string; console: boolean }
  | { page: "usage" }
  | { page: "remote"; name: string }
  | { page: "remote-instance"; name: string; instance: string }
  | { page: "notfound" };

export function parse(pathname: string): Route {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { page: "home" };
  if (parts[0] === "create" && parts.length === 1) return { page: "create" };
  if (parts[0] === "host" && parts[1] === "local" && parts.length === 2)
    return { page: "local-host" };
  if (parts[0] === "usage" && parts.length === 1) return { page: "usage" };
  if (parts[0] === "remote" && parts[1] && parts.length === 2)
    return { page: "remote", name: decodeURIComponent(parts[1]) };
  if (parts[0] === "remote" && parts[1] && parts[2] === "instance" && parts[3] && parts.length === 4)
    return { page: "remote-instance", name: decodeURIComponent(parts[1]), instance: decodeURIComponent(parts[3]) };
  if (parts[0] === "instance" && parts[1]) {
    return { page: "instance", name: decodeURIComponent(parts[1]),
             console: parts[2] === "console" };
  }
  return { page: "notfound" };
}

export const currentRoute = (): Route => parse(location.pathname);

export type HostContext = { kind: "all" } | { kind: "local" } |
  { kind: "remote"; name: string };

export function hostContext(route: Route = currentRoute()): HostContext {
  if (route.page === "remote" || route.page === "remote-instance")
    return { kind: "remote", name: route.name };
  if (route.page === "local-host" || route.page === "instance" ||
      route.page === "create" || route.page === "usage") return { kind: "local" };
  return { kind: "all" };
}

// The render callback is injected by main.ts to avoid a circular import.
let onChange: (r: Route) => void = () => {};
export function onRoute(cb: (r: Route) => void): void { onChange = cb; }

export function navigate(path: string, replace = false): void {
  if (path !== location.pathname) {
    if (replace) history.replaceState({}, "", path);
    else history.pushState({}, "", path);
  }
  onChange(currentRoute());
}

export function instancePath(name: string, console = false): string {
  const base = `/instance/${encodeURIComponent(name)}`;
  return console ? `${base}/console` : base;
}
export const localHostPath = (): string => "/host/local";
export const remotePath = (name: string): string => `/remote/${encodeURIComponent(name)}`;
export const remoteInstancePath = (name: string, instance: string): string =>
  `${remotePath(name)}/instance/${encodeURIComponent(instance)}`;

export function initRouter(): void {
  window.addEventListener("popstate", () => onChange(currentRoute()));
  // Intercept in-app links marked data-link for client-side navigation.
  document.addEventListener("click", (e) => {
    const a = (e.target as HTMLElement)?.closest?.("a[data-link]") as
      HTMLAnchorElement | null;
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (href.startsWith("/")) { e.preventDefault(); navigate(href); }
  });
}
