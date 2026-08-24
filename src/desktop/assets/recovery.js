const reason = document.querySelector("#reason");
const retry = document.querySelector("#retry");
const endpoint = document.querySelector("#endpoint");
const params = new URL(location.href).searchParams;
const message = params.get("reason");
const configuredEndpoint = params.get("endpoint");
if (reason && message) reason.textContent = message;
if (endpoint && configuredEndpoint) endpoint.textContent = configuredEndpoint;
retry?.addEventListener("click", async () => {
  /** @type {{ retryBackend?: () => Promise<void> } | undefined} */
  const desktopApi = window.sandboxDesktop;
  if (typeof desktopApi?.retryBackend !== "function") {
    if (reason) reason.textContent = "Reconnect is unavailable because the desktop bridge did not load. Restart Sandbox Desktop and try again.";
    return;
  }
  retry.disabled = true;
  retry.textContent = "Connecting…";
  try { await desktopApi.retryBackend(); }
  catch (error) {
    if (reason) reason.textContent = error instanceof Error ? error.message : "Reconnect failed";
    retry.disabled = false;
    retry.textContent = "Reconnect";
  }
});
