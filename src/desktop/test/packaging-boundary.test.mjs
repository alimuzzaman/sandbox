import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const afterPack = await readFile(new URL("../scripts/after-pack.cjs", import.meta.url), "utf8");
const smokeMac = await readFile(new URL("../scripts/smoke-mac.mjs", import.meta.url), "utf8");

test("packaging enables universal hardened macOS artifacts", () => {
  assert.equal(packageJson.build.asar, true);
  assert.equal(packageJson.build.mac.hardenedRuntime, true);
  assert.match(packageJson.scripts["package:mac"], /--universal/);
  assert.deepEqual(packageJson.build.mac.target, ["dmg", "zip"]);
  assert.match(packageJson.scripts["package:mac"], /SANDBOX_MAC_PACKAGE_MODE=local/);
  assert.match(packageJson.scripts["package:mac:signed"], /SANDBOX_MAC_PACKAGE_MODE=signed/);
  assert.match(packageJson.scripts["package:mac:signed"], /forceCodeSigning=true/);
  assert.doesNotMatch(packageJson.scripts["package:mac:signed"], /CSC_IDENTITY_AUTO_DISCOVERY=false/);
});

test("packaging disables dangerous Electron fuses", () => {
  assert.match(afterPack, /RunAsNode\]: false/);
  assert.match(afterPack, /EnableNodeOptionsEnvironmentVariable\]: false/);
  assert.match(afterPack, /EnableNodeCliInspectArguments\]: false/);
  assert.match(afterPack, /EnableEmbeddedAsarIntegrityValidation\]: true/);
  assert.match(afterPack, /OnlyLoadAppFromAsar\]: true/);
  assert.match(afterPack, /GrantFileProtocolExtraPrivileges\]: false/);
});

test("local packaging repairs invalidated ad-hoc signatures after fuse mutation", () => {
  assert.match(afterPack, /packageMode === "local" && context\.arch === Arch\.universal/);
  assert.match(afterPack, /\["--force", "--deep", "--sign", "-", appBundle\]/);
  assert.match(afterPack, /packageMode !== "local" && packageMode !== "signed"/);
  assert.ok(afterPack.indexOf("await flipFuses") < afterPack.indexOf('packageMode === "local"'));
  assert.match(packageJson.scripts["package:mac:dir"], /npm run smoke:mac/);
});

test("macOS smoke verification targets Sandbox.app and rejects raw Electron", () => {
  assert.match(smokeMac, /release\/mac-universal\/Sandbox\.app/);
  assert.match(smokeMac, /path\.basename\(resolvedBundle\), "Sandbox\.app"/);
  assert.match(smokeMac, /node_modules.*electron/);
  assert.match(smokeMac, /app\.xc1\.sandbox\.desktop/);
  assert.match(smokeMac, /codesign/);
});
