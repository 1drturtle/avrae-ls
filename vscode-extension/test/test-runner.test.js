const assert = require("node:assert/strict");
const test = require("node:test");

const { buildRunTestsInvocation, isTestFilePath, runTestFile } = require("../test-runner");

test("recognizes every supported test-file suffix case-insensitively", () => {
  for (const suffix of [".alias-test", ".aliastest", ".gvar-test", ".gvartest"]) {
    assert.equal(isTestFilePath(`/workspace/example${suffix}`), true);
    assert.equal(isTestFilePath(`/workspace/EXAMPLE${suffix.toUpperCase()}`), true);
  }
  assert.equal(isTestFilePath("/workspace/example.alias"), false);
});

test("builds a CLI invocation from the resolved server launch", () => {
  const invocation = buildRunTestsInvocation(
    {
      command: "python3",
      args: ["-m", "avrae_ls"],
      options: { env: { PYTHONPATH: "/extension/bundled/site-packages" } },
    },
    "/workspace/example.alias-test",
    "/workspace"
  );

  assert.equal(invocation.command, "python3");
  assert.deepEqual(invocation.args, ["-m", "avrae_ls", "--run-tests", "/workspace/example.alias-test"]);
  assert.deepEqual(invocation.options, {
    cwd: "/workspace",
    env: { PYTHONPATH: "/extension/bundled/site-packages" },
    windowsHide: true,
  });
});

test("returns process output and exit status", async () => {
  const result = await runTestFile(
    { command: "avrae-ls", args: ["--run-tests", "test.alias-test"], options: {} },
    (_command, _args, _options, callback) => callback(Object.assign(new Error("failed"), { code: 1 }), "report", "details")
  );

  assert.equal(result.exitCode, 1);
  assert.equal(result.stdout, "report");
  assert.equal(result.stderr, "details");
  assert.equal(result.launchError, undefined);
});
