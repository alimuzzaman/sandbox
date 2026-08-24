const reason = document.querySelector("#reason");
const retry = document.querySelector("#retry");
const message = new URL(location.href).searchParams.get("reason");
if (reason && message) reason.textContent = message;
retry?.addEventListener("click", async () => {
  retry.disabled = true;
  retry.textContent = "Connecting…";
  try { await window.sandboxDesktop.retryBackend(); }
  catch (error) {
    if (reason) reason.textContent = error instanceof Error ? error.message : "Reconnect failed";
    retry.disabled = false;
    retry.textContent = "Reconnect";
  }
});
