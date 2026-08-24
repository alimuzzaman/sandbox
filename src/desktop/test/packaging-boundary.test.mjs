import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const afterPack = await readFile(new URL("../scripts/after-pack.cjs", import.meta.url), "utf8");

test("packaging enables universal hardened macOS artifacts", () => {
  assert.equal(packageJson.build.asar, true);
  assert.equal(packageJson.build.mac.hardenedRuntime, true);
  assert.match(packageJson.scripts["package:mac"], /--universal/);
  assert.deepEqual(packageJson.build.mac.target, ["dmg", "zip"]);
});

test("packaging disables dangerous Electron fuses", () => {
  assert.match(afterPack, /RunAsNode\]: false/);
  assert.match(afterPack, /EnableNodeOptionsEnvironmentVariable\]: false/);
  assert.match(afterPack, /EnableNodeCliInspectArguments\]: false/);
  assert.match(afterPack, /EnableEmbeddedAsarIntegrityValidation\]: true/);
  assert.match(afterPack, /OnlyLoadAppFromAsar\]: true/);
  assert.match(afterPack, /GrantFileProtocolExtraPrivileges\]: false/);
});
