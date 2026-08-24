import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { access, realpath } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const expectedBundleId = "app.xc1.sandbox.desktop";
const appBundle = path.resolve(process.argv.find((argument) => argument.endsWith(".app")) ?? "release/mac-universal/Sandbox.app");
const shouldLaunch = process.argv.includes("--launch");
const shouldOpen = process.argv.includes("--open");

if (process.platform !== "darwin") {
  throw new Error("The packaged macOS app smoke check requires macOS");
}

const resolvedBundle = await realpath(appBundle);
assert.equal(path.basename(resolvedBundle), "Sandbox.app", `Expected the packaged Sandbox.app, received ${resolvedBundle}`);
assert.ok(!resolvedBundle.includes(`${path.sep}node_modules${path.sep}electron${path.sep}`), "Refusing to validate Electron's raw node_modules runtime");

const infoPlist = path.join(resolvedBundle, "Contents", "Info.plist");
const executable = path.join(resolvedBundle, "Contents", "MacOS", "Sandbox");
const appAsar = path.join(resolvedBundle, "Contents", "Resources", "app.asar");
await Promise.all([access(infoPlist), access(executable), access(appAsar)]);

const { stdout: bundleId } = await execFileAsync("plutil", ["-extract", "CFBundleIdentifier", "raw", "-o", "-", infoPlist]);
assert.equal(bundleId.trim(), expectedBundleId, `Refusing unexpected app bundle ${bundleId.trim()}`);
await execFileAsync("codesign", ["--verify", "--deep", "--strict", "--verbose=2", resolvedBundle]);

const framework = path.join(resolvedBundle, "Contents", "Frameworks", "Electron Framework.framework", "Versions", "A", "Electron Framework");
const { stdout: architectures } = await execFileAsync("lipo", ["-archs", framework]);
for (const architecture of ["x86_64", "arm64"]) {
  assert.match(architectures, new RegExp(`(^|\\s)${architecture}(\\s|$)`), `Missing ${architecture} from universal Electron Framework`);
}

if (shouldLaunch) {
  await verifyLaunch(executable);
} else if (shouldOpen) {
  await execFileAsync("open", ["-n", resolvedBundle]);
}

console.log(`Validated packaged Sandbox.app (${architectures.trim()}; bundle ${expectedBundleId})`);

async function verifyLaunch(binary) {
  const child = spawn(binary, [], { stdio: "ignore" });
  const earlyExit = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  const stayedAlive = await Promise.race([
    earlyExit.then((result) => ({ result })),
    new Promise((resolve) => setTimeout(() => resolve({ result: null }), 5000)),
  ]);
  assert.equal(stayedAlive.result, null, `Sandbox.app exited during launch smoke check: ${JSON.stringify(stayedAlive.result)}`);

  child.kill("SIGTERM");
  const stopped = await Promise.race([
    earlyExit.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 3000)),
  ]);
  if (!stopped) {
    child.kill("SIGKILL");
    await earlyExit;
  }
}
