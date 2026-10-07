"use strict";
// Only disposable Electron user data and a synthetic loopback server are used.
const { _electron } = require("playwright"), assert = require("node:assert/strict"), fs = require("node:fs/promises"), os = require("node:os"), path = require("node:path"), http = require("node:http");
(async () => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "markitdown-desktop-"));
  const output = process.env.MARKITDOWN_TEST_EVIDENCE;
  let requests = 0;
  let compatible = false;
  const server = http.createServer((request, response) => { requests += 1; if (request.url === "/api/config") { response.setHeader("Content-Type", "application/json"); setTimeout(() => response.end(JSON.stringify({ extensions: [".md"], has_admin: true })), 600); } else { response.setHeader("Content-Type", "text/html"); response.statusCode = compatible ? 200 : 404; response.end("<!doctype html><title>Synthetic service</title><h1>Connected</h1>"); } });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let app;
  try {
    app = await _electron.launch({ args: [path.resolve(__dirname, "..")], env: { ...process.env, MARKITDOWN_TEST_DATA_DIR: data, MARKITDOWN_TEST_BACKGROUND: "1" } });
    const setup = await app.firstWindow();
    await setup.locator("#service-origin").fill("http://remote.example.com");
    await setup.locator("#connect-button").click();
    await setup.waitForFunction(() => document.getElementById("status").textContent.includes("HTTPS"));
    assert.equal(requests, 0);
    await setup.locator("#service-origin").fill(`http://127.0.0.1:${server.address().port}`);
    await setup.locator("#connect-button").click();
    await setup.waitForFunction(() => document.getElementById("status").textContent.includes("升级服务"));
    assert.equal(app.windows().length, 1);
    compatible = true;
    await setup.locator("#connect-button").click();
    await setup.locator("#cancel-button").click();
    await setup.waitForFunction(() => document.getElementById("status").textContent === "已取消连接。");
    await setup.waitForTimeout(800);
    assert.equal(app.windows().length, 1);
    if (output) { await fs.mkdir(output, { recursive: true }); await setup.screenshot({ path: path.join(output, "desktop-connect.png") }); }
    requests = 0;
    const deskPromise = app.waitForEvent("window");
    await setup.evaluate(() => { const form = document.getElementById("connect-form"); form.dispatchEvent(new Event("submit", { cancelable: true })); form.dispatchEvent(new Event("submit", { cancelable: true })); });
    await setup.waitForTimeout(1200);
    console.log("Connection status:", await setup.locator("#status").textContent(), "requests:", requests);
    const desk = await deskPromise;
    await desk.waitForSelector("h1");
    assert.equal(await desk.locator("h1").textContent(), "Connected");
    assert.equal(requests, 3); // one config, workspace probe and workspace, no duplicate connect
    assert.equal(await desk.evaluate(() => typeof window.desktop), "undefined");
    assert.equal(await desk.evaluate(() => typeof window.require), "undefined");
    const prefs = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.webContents.getLastWebPreferences()));
    assert(prefs.every((p) => p.sandbox && p.contextIsolation && !p.nodeIntegration && p.webSecurity));
    await desk.evaluate(() => { window.location.href = "https://example.com/"; });
    await desk.waitForTimeout(200);
    assert(desk.url().startsWith("http://127.0.0.1:"));
    await app.evaluate(({ Menu }) => Menu.getApplicationMenu().items.find((item) => item.label === "工作台").submenu.items[0].click());
    await setup.waitForFunction(() => document.getElementById("status").textContent.includes("已断开连接"));
    assert.equal(app.windows().length, 1);
    console.log("Desktop smoke passed: HTTPS rejection, cancel, repeat submit, real window, sandbox/no bridge, navigation block, return.");
  } finally { await app?.close(); await new Promise((resolve) => server.close(resolve)); await fs.rm(data, { recursive: true, force: true }); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
