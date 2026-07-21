const path = require("path");

function bundledSitePackages(extensionPath, platform = process.platform) {
  const pathModule = platform === "win32" ? path.win32 : path;
  return pathModule.join(extensionPath, "bundled", "site-packages");
}

function resolveServerLaunch({ extensionPath, overridePath = "", platform = process.platform, environment = process.env }) {
  const configuredPath = typeof overridePath === "string" ? overridePath.trim() : "";
  if (configuredPath) {
    return {
      command: configuredPath,
      args: [],
      options: {},
      usesBundledServer: false,
    };
  }

  const sitePackages = bundledSitePackages(extensionPath, platform);
  const pathDelimiter = platform === "win32" ? ";" : path.delimiter;
  const pythonPath = [sitePackages, environment.PYTHONPATH].filter(Boolean).join(pathDelimiter);
  return {
    command: platform === "win32" ? "python" : "python3",
    args: ["-m", "avrae_ls"],
    options: {
      env: {
        ...environment,
        PYTHONPATH: pythonPath,
      },
    },
    usesBundledServer: true,
  };
}

function isCompatiblePythonVersion(version) {
  const match = /^(\d+)\.(\d+)/.exec(String(version).trim());
  if (!match) return false;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  return major > 3 || (major === 3 && minor >= 11);
}

module.exports = {
  bundledSitePackages,
  isCompatiblePythonVersion,
  resolveServerLaunch,
};
