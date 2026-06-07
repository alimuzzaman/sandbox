// History-API router with clean URLs. The current view is a pure function of
// location.pathname — there is no separate `selected`/`viewMode` state.
//
//   /                        -> home (welcome, or auto-first instance)
//   /instance/<name>         -> instance detail
//   /instance/<name>/console -> instance detail + console drawer open
//   /usage                   -> Claude usage page
//
// On hard-refresh/deep-link the Python server serves the app for any non-/api
// path (SPA fallback), then this router renders the right view.

export type Route =
  | { page: "home" }
  | { page: "create" }
  | { page: "instance"; name: string; console: boolean }
  | { page: "usage" }
  | { page: "notfound" };

export function parse(pathname: string): Route {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { page: "home" };
  if (parts[0] === "create" && parts.length === 1) return { page: "create" };
  if (parts[0] === "usage" && parts.length === 1) return { page: "usage" };
  if (parts[0] === "instance" && parts[1]) {
    return { page: "instance", name: decodeURIComponent(parts[1]),
             console: parts[2] === "console" };
  }
  return { page: "notfound" };
}

export const currentRoute = (): Route => parse(location.pathname);

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
