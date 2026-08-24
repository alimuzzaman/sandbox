const { flipFuses, FuseVersion, FuseV1Options } = require("@electron/fuses");
const { Arch } = require("electron-builder");
const { execFile } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);

module.exports = async function afterPack(context) {
  const appBundle = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  const executable = path.join(appBundle, "Contents", "MacOS", context.packager.appInfo.productFilename);
  await flipFuses(executable, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  });

  const packageMode = process.env.SANDBOX_MAC_PACKAGE_MODE;
  if (packageMode === "local" && context.arch === Arch.universal) {
    // Electron's downloaded binaries carry ad-hoc signatures. Universal merging and
    // fuse mutation invalidate them, so repair the complete local bundle after both.
    // This is not a distributable Developer ID signature or a release substitute.
    await execFileAsync("codesign", ["--force", "--deep", "--sign", "-", appBundle]);
  } else if (packageMode !== "local" && packageMode !== "signed") {
    throw new Error("Set SANDBOX_MAC_PACKAGE_MODE=local for an ad-hoc local build or =signed for a Developer ID release build");
  }
};
