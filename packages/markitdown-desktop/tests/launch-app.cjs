"use strict";
const { _electron } = require("playwright"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
async function launchApp(directory) {
  const executable = process.env.MARKITDOWN_TEST_EXECUTABLE;
  const packaged = !!executable;
  if (packaged && (!path.isAbsolute(executable) || !fs.statSync(executable).isFile())) throw new Error("Expected a real absolute packaged executable path.");
  const app = await _electron.launch({ ...(packaged ? { executablePath: executable } : {}),
    args: packaged ? [`--user-data-dir=${directory}`] : [path.resolve(__dirname, "..")],
    env: { ...process.env, MARKITDOWN_TEST_DATA_DIR: directory, MARKITDOWN_TEST_BACKGROUND: "1" },
    chromiumSandbox: true, timeout: 30000 });
  try {
    const state = await app.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath("userData"), sessionData: app.getPath("sessionData") }));
    assert.equal(state.packaged, packaged);
    assert.equal(fs.realpathSync(state.userData), fs.realpathSync(directory));
    if (packaged) assert.equal(fs.realpathSync(state.sessionData), fs.realpathSync(directory));
    console.log(packaged ? "Testing actual packaged application with isolated data." : "Testing development application with isolated data.");
    return app;
  } catch (error) { await app.close(); throw error; }
}
module.exports = { launchApp };
