const { execFile } = require("child_process");

const TEST_FILE_SUFFIXES = [".alias-test", ".aliastest", ".gvar-test", ".gvartest"];

function isTestFilePath(filePath) {
  const normalizedPath = String(filePath || "").toLowerCase();
  return TEST_FILE_SUFFIXES.some((suffix) => normalizedPath.endsWith(suffix));
}

function buildRunTestsInvocation(serverLaunch, testFilePath, cwd) {
  return {
    command: serverLaunch.command,
    args: [...serverLaunch.args, "--run-tests", testFilePath],
    options: {
      cwd,
      env: serverLaunch.options.env,
      windowsHide: true,
    },
  };
}

function runTestFile(invocation, executeFile = execFile) {
  return new Promise((resolve) => {
    executeFile(invocation.command, invocation.args, invocation.options, (error, stdout, stderr) => {
      resolve({
        stdout: stdout || "",
        stderr: stderr || "",
        exitCode: typeof error?.code === "number" ? error.code : 0,
        launchError: error && typeof error.code !== "number" ? error : undefined,
      });
    });
  });
}

module.exports = {
  TEST_FILE_SUFFIXES,
  buildRunTestsInvocation,
  isTestFilePath,
  runTestFile,
};
