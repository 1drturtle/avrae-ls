const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const { bundledSitePackages, isCompatiblePythonVersion, resolveServerLaunch } = require("../server-launcher");

test("uses the bundled package with the platform Python command", () => {
  const launch = resolveServerLaunch({
    extensionPath: "/extension",
    platform: "linux",
    environment: { PYTHONPATH: "/existing", KEEP: "value" },
  });

  assert.equal(launch.command, "python3");
  assert.deepEqual(launch.args, ["-m", "avrae_ls"]);
  assert.equal(launch.usesBundledServer, true);
  assert.equal(launch.options.env.PYTHONPATH, `/extension/bundled/site-packages${path.delimiter}/existing`);
  assert.equal(launch.options.env.KEEP, "value");
});

test("uses python on Windows", () => {
  const launch = resolveServerLaunch({
    extensionPath: "C:\\extension",
    platform: "win32",
    environment: { PYTHONPATH: "C:\\existing" },
  });

  assert.equal(launch.command, "python");
  assert.deepEqual(launch.args, ["-m", "avrae_ls"]);
  assert.equal(launch.options.env.PYTHONPATH, "C:\\extension\\bundled\\site-packages;C:\\existing");
});

test("an override path takes precedence over the bundled server", () => {
  const launch = resolveServerLaunch({
    extensionPath: "/extension",
    overridePath: "  /workspace/.venv/bin/avrae-ls  ",
    environment: { PYTHONPATH: "/existing" },
  });

  assert.equal(launch.command, "/workspace/.venv/bin/avrae-ls");
  assert.deepEqual(launch.args, []);
  assert.deepEqual(launch.options, {});
  assert.equal(launch.usesBundledServer, false);
});

test("constructs the expected bundled package path", () => {
  assert.equal(bundledSitePackages("/extension"), "/extension/bundled/site-packages");
});

test("requires Python 3.11 or newer", () => {
  assert.equal(isCompatiblePythonVersion("3.10.14"), false);
  assert.equal(isCompatiblePythonVersion("3.11.0"), true);
  assert.equal(isCompatiblePythonVersion("3.14.0"), true);
  assert.equal(isCompatiblePythonVersion("not-python"), false);
});
