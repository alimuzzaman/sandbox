import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mainSource = await readFile(new URL("../src/main.ts", import.meta.url), "utf8");
const preloadSource = await readFile(new URL("../src/preload.ts", import.meta.url), "utf8");
const contractsSource = await readFile(new URL("../src/contracts.ts", import.meta.url), "utf8");

test("BrowserWindow keeps the renderer isolated and sandboxed", () => {
  assert.match(mainSource, /contextIsolation:\s*true/);
  assert.match(mainSource, /nodeIntegration:\s*false/);
  assert.match(mainSource, /sandbox:\s*true/);
  assert.match(mainSource, /webSecurity:\s*true/);
  assert.match(mainSource, /setPermissionRequestHandler/);
  assert.match(mainSource, /event\.senderFrame === mainWindow\.webContents\.mainFrame/);
});

test("preload exposes only the typed project-directory picker", () => {
  assert.match(preloadSource, /exposeInMainWorld\("sandboxDesktop", api\)/);
  assert.match(contractsSource, /chooseProjectDirectory/);
  assert.doesNotMatch(contractsSource, /exec|spawn|shell|ssh|docker|token|readFile|writeFile/i);
});
