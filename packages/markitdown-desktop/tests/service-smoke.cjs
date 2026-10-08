"use strict";
// Invoked by scripts/verify-desktop-service.py, which owns the server and data.
const { launchApp } = require("./launch-app.cjs"), assert = require("node:assert/strict"), fs = require("node:fs/promises"), os = require("node:os"), path = require("node:path");

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
  const origin = process.env.MARKITDOWN_SYNTHETIC_ORIGIN;
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin || "") || origin.endsWith(":8765")) throw new Error("Only the owned ephemeral synthetic server is supported");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "markitdown-native-service-"));
  const output = process.env.MARKITDOWN_TEST_EVIDENCE;
  await fs.mkdir(output, { recursive: true });
  let app;
  try {
    app = await launchApp(data);
    const setup = await app.firstWindow();
    await setup.locator("#service-origin").fill(origin);
    const ready = app.waitForEvent("window");
    await setup.locator("#connect-button").click();
    const desk = await ready;
    await desk.waitForSelector("#login-button:not([disabled])");
    await desk.locator("#login-username").fill("demo-reader");
    await desk.locator("#login-password").fill(process.env.MARKITDOWN_SYNTHETIC_PASSWORD);
    await desk.locator("#login-button").click();
    await desk.waitForSelector("#session-section:not([hidden])");
    assert.equal(await desk.locator("#admin-toggle").isVisible(), false);
    const contents = "# 项目资料整理\n\n把文档整理为可继续编辑的 Markdown。\n\n## 本周工作\n\n| 事项 | 状态 |\n| --- | --- |\n| 汇总项目资料 | 已整理 |\n| 核对关键数字 | 待复核 |\n| 导出与归档 | 待完成 |\n\n## 复核清单\n\n- 对照原文核对数字与单位\n- 检查表格结构与阅读顺序\n- 在任务到期前保存结果\n\n> 这是用于界面验收的合成示例，不包含真实用户资料。\n";
    await desk.locator("#file-input").setInputFiles({ name: "项目简报.md", mimeType: "text/markdown", buffer: Buffer.from(contents) });
    await probeRendererZoom(app, desk, [1440, 960], ["#nav-workspace", "#choose-button", "#convert-button"], output, "workspace-submit");
    await desk.evaluate(() => { document.getElementById("convert-button").click(); document.getElementById("convert-button").click(); });
    await desk.waitForSelector("#download-button:not([disabled])", { timeout: 90000 });
    const usage = await desk.evaluate(async () => (await (await fetch("/api/me")).json()).usage);
    assert.equal(usage.used, 1);
    assert.equal(await desk.locator("#history-list li").count(), 1);
    await probeRendererZoom(app, desk, [1440, 960], ['#history-list button[data-action="select"]', "#preview-tab", "#download-button", "#archive-button"], output, "workspace-read-export");
    await desk.evaluate(() => window.scrollTo(0, 0));
    await desk.waitForTimeout(120);
    await desk.screenshot({ path: path.join(output, "desktop-workspace.png") });
    await desk.locator("#nav-history").click();
    assert.equal(await desk.locator("#upload-section").isVisible(), false);
    await desk.screenshot({ path: path.join(output, "desktop-history.png") });
    await desk.locator("#source-tab").click();
    assert.equal(await desk.locator("#markdown-source").inputValue(), contents);
    // Electron still emits its DownloadItem. Capture a deterministic native
    // save destination in this test only; the shipped app uses the save dialog.
    const destination = path.join(data, "saved-result.md");
    await app.evaluate(({ BrowserWindow }, destination) => {
      const window = BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith("http:"));
      global.__downloadDone = new Promise((resolve) => window.webContents.session.once("will-download", (_event, item) => {
        item.setSavePath(destination); item.once("done", (_event, state) => resolve(state));
      }));
    }, destination);
    await desk.locator("#download-button").click();
    assert.equal(await app.evaluate(async () => await global.__downloadDone), "completed");
    assert.equal(await fs.readFile(destination, "utf8"), contents);
    await fs.copyFile(destination, path.join(output, "native-result.md"));
    // A cancelled DownloadItem must leave the result available and quota intact.
    await app.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith("http:"));
      window.webContents.session.once("will-download", (_event, item) => item.cancel());
    });
    await desk.locator("#download-button").click();
    await desk.waitForTimeout(250);
    assert.equal(await desk.locator("#markdown-source").inputValue(), contents);
    await desk.locator("#nav-workspace").click();
    await desk.locator("#split-tab").click();
    await desk.evaluate(() => window.scrollTo(0, 0));
    await desk.waitForTimeout(120);
    await desk.screenshot({ path: path.join(output, "desktop-compare.png") });
    assert.equal(await desk.evaluate(() => typeof window.desktop), "undefined");
    // Hold a real detail response, then logout before it returns.
    let release;
    const hold = new Promise((resolve) => { release = resolve; });
    let held;
    const started = new Promise((resolve) => { held = resolve; });
    await desk.route("**/api/jobs/*", async (route) => {
      if (route.request().method() !== "GET") { await route.continue(); return; }
      const response = await route.fetch(); held(); await hold; await route.fulfill({ response });
    });
    await desk.locator('#history-list button[data-action="select"]').click();
    await started;
    await desk.locator("#logout-button").click();
    release();
    await desk.waitForSelector("#auth-section:not([hidden])");
    await desk.waitForTimeout(250);
    assert.equal(await desk.locator("#markdown-source").inputValue(), "");
    assert.equal(await desk.locator("#markdown-preview").textContent(), "");
    await desk.evaluate(() => window.scrollTo(0, 0));
    await desk.waitForTimeout(120);
    await desk.screenshot({ path: path.join(output, "desktop-logged-out.png") });
    await app.evaluate(({ Menu }) => Menu.getApplicationMenu().items.find((item) => item.label === "工作台").submenu.items[0].click());
    await setup.waitForFunction(() => document.getElementById("status").textContent.includes("已断开连接"));
    console.log("Native service checks passed: 200% renderer zoom/reflow, real member login/conversion, repeat guard/quota, history/source, native download bytes/cancel, interrupted detail/logout privacy, return.");
  } finally { await app?.close(); await fs.rm(data, { recursive: true, force: true }); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
