// The desktop rail becomes a disclosure on narrow screens.
export function initNavigation(): () => void {
  const toggle = document.getElementById("navToggle") as HTMLButtonElement | null;
  const sidebar = document.getElementById("appSidebar");
  const backdrop = document.getElementById("navBackdrop");
  const content = document.getElementById("appContent");
  const consolePanel = document.getElementById("console");
  const narrow = window.matchMedia("(max-width: 767px)");
  let open = false;
  const setOpen = (value: boolean): void => {
    open = narrow.matches && value;
    document.body.classList.toggle("nav-open", open);
    toggle?.setAttribute("aria-expanded", String(open));
    // Keep keyboard navigation inside the visible drawer while it overlays
    // the page. Closing restores the ordinary page tab order.
    if (content) content.inert = open;
    if (consolePanel) consolePanel.inert = open;
  };
  const close = (): void => {
    const restore = open && narrow.matches && sidebar?.contains(document.activeElement);
    setOpen(false);
    if (restore) toggle?.focus();
  };
  toggle?.addEventListener("click", () => setOpen(!open));
  backdrop?.addEventListener("click", close);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && open) { event.preventDefault(); close(); toggle?.focus(); }
  });
  // Close after ordinary sidebar actions, including handlers that stop
  // bubbling. Modified link clicks keep native new-tab behavior.
  sidebar?.addEventListener("click", event => {
    const mouse = event as MouseEvent;
    const target = event.target;
    if (!mouse.metaKey && !mouse.ctrlKey && !mouse.shiftKey && !mouse.altKey &&
        target instanceof Element && target.closest("a[data-link], #newBtn, #helpBtn, #termBtn")) {
      queueMicrotask(close);
    }
  }, true);
  narrow.addEventListener("change", close);
  setOpen(false);
  return close;
}
