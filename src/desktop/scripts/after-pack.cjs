const { flipFuses, FuseVersion, FuseV1Options } = require("@electron/fuses");
const path = require("node:path");

module.exports = async function afterPack(context) {
  const executable = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, "Contents", "MacOS", context.packager.appInfo.productFilename);
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
};
