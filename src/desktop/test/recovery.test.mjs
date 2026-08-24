import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const recoverySource = await readFile(new URL("../assets/recovery.js", import.meta.url), "utf8");

function loadRecovery(href, sandboxDesktop) {
  let click;
  const elements = {
    "#reason": { textContent: "Waiting for the local backend…" },
    "#endpoint": { textContent: "Unavailable" },
    "#retry": {
      disabled: false,
      textContent: "Reconnect",
      addEventListener(name, listener) {
        if (name === "click") click = listener;
      },
    },
  };
  const context = {
    document: { querySelector: (selector) => elements[selector] ?? null },
    location: { href },
    URL,
    window: sandboxDesktop ? { sandboxDesktop } : {},
  };
  vm.runInNewContext(recoverySource, context);
  return { elements, click };
}

test("recovery remains usable when the preload bridge is unavailable", async () => {
  const { elements, click } = loadRecovery(
    "sandbox-app://app/recovery.html?reason=Backend%20down&endpoint=http%3A%2F%2Flocalhost%3A5199",
  );

  assert.equal(elements["#reason"].textContent, "Backend down");
  assert.equal(elements["#endpoint"].textContent, "http://localhost:5199");
  await assert.doesNotReject(click());
  assert.match(elements["#reason"].textContent, /desktop bridge did not load/);
  assert.equal(elements["#retry"].disabled, false);
});

test("recovery invokes the preload retry operation when available", async () => {
  let retries = 0;
  const { elements, click } = loadRecovery(
    "sandbox-app://app/recovery.html?endpoint=http%3A%2F%2F127.0.0.1%3A9999",
    { retryBackend: async () => { retries += 1; } },
  );

  await click();
  assert.equal(retries, 1);
  assert.equal(elements["#retry"].disabled, true);
  assert.equal(elements["#retry"].textContent, "Connecting…");
  assert.equal(elements["#endpoint"].textContent, "http://127.0.0.1:9999");
});
