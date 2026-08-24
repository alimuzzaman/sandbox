import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mainSource = await readFile(new URL("../src/main.ts", import.meta.url), "utf8");
const preloadSource = await readFile(new URL("../src/preload.ts", import.meta.url), "utf8");
const compiledPreloadSource = await readFile(new URL("../dist/preload.js", import.meta.url), "utf8");
const contractsSource = await readFile(new URL("../src/contracts.ts", import.meta.url), "utf8");

test("BrowserWindow keeps the renderer isolated and sandboxed", () => {
  assert.match(mainSource, /contextIsolation:\s*true/);
  assert.match(mainSource, /nodeIntegration:\s*false/);
  assert.match(mainSource, /sandbox:\s*true/);
  assert.match(mainSource, /webSecurity:\s*true/);
  assert.match(mainSource, /setPermissionRequestHandler/);
  assert.match(mainSource, /event\.senderFrame === mainWindow\.webContents\.mainFrame/);
});

test("preload exposes only typed project and recovery operations", () => {
  assert.match(preloadSource, /exposeInMainWorld\("sandboxDesktop", api\)/);
  assert.doesNotMatch(compiledPreloadSource, /require\("\.\/contracts"\)/);
  assert.match(contractsSource, /chooseProjectDirectory/);
  assert.match(contractsSource, /retryBackend/);
  assert.doesNotMatch(contractsSource, /exec|spawn|shell|ssh|docker|token|readFile|writeFile/i);
});

test("main process pins navigation, IPC senders, and one app instance", () => {
  assert.match(mainSource, /requestSingleInstanceLock/);
  assert.match(mainSource, /will-navigate/);
  assert.match(mainSource, /will-attach-webview/);
  assert.match(mainSource, /setWindowOpenHandler/);
  assert.match(mainSource, /event\.senderFrame === mainWindow\.webContents\.mainFrame/);
  assert.match(mainSource, /setPermissionCheckHandler/);
});

test("recovery receives only the validated configured backend origin", () => {
  assert.match(mainSource, /configuredBackend\(\)\.origin/);
  assert.match(mainSource, /searchParams\.set\("endpoint", backendOrigin\)/);
  assert.doesNotMatch(mainSource, /process\.env\.SANDBOX_DESKTOP_URL[^\n]*searchParams/);
});
