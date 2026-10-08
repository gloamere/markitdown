"use strict";
// Only disposable Electron user data and a synthetic loopback server are used.
const { launchApp } = require("./launch-app.cjs"), assert = require("node:assert/strict"), fs = require("node:fs/promises"), os = require("node:os"), path = require("node:path"), http = require("node:http");

// This is real Electron renderer zoom, not a narrow viewport called manual zoom.
// Only this disposable test window is resized; restore its bounds and 100% zoom.
async function probeRendererZoom(app, page, widths, selectors, output, label) {
  const native = await app.browserWindow(page);
  const bounds = await native.evaluate((window) => window.getBounds());
  assert.equal(await native.evaluate((window) => window.webContents.getZoomFactor()), 1);
  const evidence = [];
  try {
    for (const width of widths) {
      await native.evaluate((window, width) => { window.setContentSize(width, 820); window.webContents.setZoomFactor(2); }, width);
      await page.waitForFunction((expected) => innerWidth === expected, width / 2);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await native.evaluate((window) => window.webContents.getZoomFactor()), 2);
      const dimensions = await page.evaluate(() => ({ viewport: innerWidth, height: innerHeight, scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) }));
      assert(dimensions.scroll <= dimensions.viewport + 1, `${label}: page overflow at 200%: ${JSON.stringify(dimensions)}`);
      if (label === "setup") assert(await page.evaluate(() => {
        const story = document.querySelector(".story").getBoundingClientRect(), connection = document.querySelector(".connection").getBoundingClientRect();
        return story.bottom <= connection.top;
      }), "Setup must stack into one column at 200% renderer zoom");
      const reached = [];
      for (const selector of selectors) {
        const control = page.locator(selector);
        assert(await control.isVisible(), `${label}: hidden ${selector}`);
        await control.scrollIntoViewIfNeeded();
        const geometry = await control.evaluate((node) => {
          const rect = node.getBoundingClientRect(), x = (rect.left + rect.right) / 2, y = (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2;
          return { fits: rect.left >= -1 && rect.right <= innerWidth + 1 && rect.top >= -1 && rect.bottom <= innerHeight + 1, unobscured: node.contains(document.elementFromPoint(x, y)) };
        });
        assert(geometry.fits && geometry.unobscured, `${label}: clipped or covered ${selector} at ${dimensions.viewport}px`);
        // Enabled primary controls must also remain reachable with real Tab keys.
        if (await control.isEnabled()) {
          for (let tabs = 0; tabs < 80 && !(await control.evaluate((node) => node === document.activeElement)); tabs += 1) await page.keyboard.press("Tab");
          assert(await control.evaluate((node) => node === document.activeElement), `${label}: Tab cannot reach ${selector}`);
          reached.push(selector);
        }
      }
      evidence.push({ ...dimensions, content_width: width, zoom_factor: 2, keyboard_reached: reached });
      if (output) { await fs.mkdir(output, { recursive: true }); await page.screenshot({ path: path.join(output, `${label}-${width / 2}px-200percent.png`), fullPage: true }); }
    }
  } finally {
    await native.evaluate((window, bounds) => { window.webContents.setZoomFactor(1); window.setBounds(bounds); }, bounds);
    assert.equal(await native.evaluate((window) => window.webContents.getZoomFactor()), 1);
    await native.dispose();
  }
  if (output) await fs.writeFile(path.join(output, `${label}-renderer-zoom.json`), JSON.stringify({ method: "isolated BrowserWindow.webContents.setZoomFactor(2)", manual_keyboard_zoom: "not tested", visual_review: "pending", probes: evidence }, null, 2) + "\n");
  console.log(`${label} renderer zoom/reflow passed:`, JSON.stringify(evidence));
}

(async () => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "markitdown-desktop-"));
  const output = process.env.MARKITDOWN_TEST_EVIDENCE;
  let requests = 0;
  let compatible = false;
  const server = http.createServer((request, response) => { requests += 1; if (request.url === "/api/config") { response.setHeader("Content-Type", "application/json"); setTimeout(() => response.end(JSON.stringify({ extensions: [".md"], has_admin: true })), 600); } else { response.setHeader("Content-Type", "text/html"); response.statusCode = compatible ? 200 : 404; response.end("<!doctype html><title>Synthetic service</title><h1>Connected</h1>"); } });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let app;
  try {
    app = await launchApp(data);
    const setup = await app.firstWindow();
    const info = await setup.evaluate(() => window.desktop.info());
    await setup.waitForFunction(() => document.getElementById("version").textContent.length > 0);
    assert((await setup.locator("#distribution-status").textContent()).includes(info.distribution === "developer-id-unnotarized" ? "Developer ID 已签名 / 未公证" : "未签名 / 未公证"));
    await probeRendererZoom(app, setup, [1060, 760], ["#service-origin", "#connect-button"], output, "setup");
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
    const expectedTitle = `MarkItDown · http://127.0.0.1:${server.address().port}`;
    const nativeTitle = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((window) => window.webContents.getURL().startsWith("http://127.0.0.1:")).getTitle());
    assert.equal(await nativeTitle(), expectedTitle);
    await desk.evaluate(() => { document.title = "Local offline conversion"; });
    await desk.waitForTimeout(100);
    assert.equal(await nativeTitle(), expectedTitle);
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
    console.log("Desktop smoke passed: 200% renderer zoom/reflow, HTTPS rejection, cancel, repeat submit, real window, sandbox/no bridge, navigation block, return.");
  } finally { await app?.close(); await new Promise((resolve) => server.close(resolve)); await fs.rm(data, { recursive: true, force: true }); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
