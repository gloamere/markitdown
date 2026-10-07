"use strict";
const { app, BrowserWindow, Menu, dialog, ipcMain, protocol, net, session } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { randomUUID } = require("node:crypto");
const { serviceOrigin, sameService, allowedDownload, safeFilename } = require("./policy.cjs");

protocol.registerSchemesAsPrivileged([{ scheme: "markitdown", privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.enableSandbox();
if (!app.isPackaged && process.env.MARKITDOWN_TEST_DATA_DIR) app.setPath("userData", path.resolve(process.env.MARKITDOWN_TEST_DATA_DIR));
let setup, desk, serviceSession, currentOrigin = null, connecting = false, generation = 0;
let lastOrigin = "", startupMessage = "";
let connectionAbort = null;
const setupURL = "markitdown://desktop/setup.html";
const settingsPath = () => path.join(app.getPath("userData"), "service.json");
const preferences = { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, webviewTag: false, allowRunningInsecureContent: false };
function present(window) { if (process.env.MARKITDOWN_TEST_BACKGROUND === "1") window.showInactive(); else window.show(); }

function lockContents(contents, origin) {
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-attach-webview", (event) => event.preventDefault());
  const navigate = (event, url) => { if (!sameService(url, origin)) event.preventDefault(); };
  contents.on("will-navigate", navigate);
  contents.on("will-redirect", navigate);
}

function trustedSetup(event) {
  return !!setup && !setup.isDestroyed() && event.sender === setup.webContents && event.senderFrame === setup.webContents.mainFrame && event.senderFrame.url === setupURL;
}

async function disconnect() {
  generation += 1;
  connectionAbort?.abort();
  connectionAbort = null;
  currentOrigin = null;
  if (desk && !desk.isDestroyed()) { desk.removeAllListeners("closed"); desk.destroy(); }
  desk = null;
  if (serviceSession) {
    const old = serviceSession;
    serviceSession = null;
    old.closeAllConnections();
    await old.clearStorageData();
    await old.clearCache();
  }
}

async function showSetup(message = "") {
  await disconnect();
  startupMessage = message;
  if (!setup || setup.isDestroyed()) {
    setup = new BrowserWindow({ width: 1060, height: 820, minWidth: 760, minHeight: 700, backgroundColor: "#f6f7fb", title: "MarkItDown · 连接工作台", show: false,
      webPreferences: { ...preferences, preload: path.join(__dirname, "preload.cjs"), partition: "markitdown-setup" } });
    setup.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    setup.webContents.on("will-navigate", (event) => event.preventDefault());
    setup.webContents.on("will-redirect", (event) => event.preventDefault());
    setup.on("closed", () => { setup = null; });
    setup.once("ready-to-show", () => { if (setup) present(setup); });
    await setup.loadURL(setupURL);
  } else {
    setup.webContents.send("connection-status", message);
    present(setup);
  }
}

async function connect(value) {
  if (connecting) return { ok: false, message: "正在连接，请稍候。" };
  connecting = true;
  let candidate;
  try {
    const origin = serviceOrigin(value);
    await disconnect();
    const attempt = generation;
    serviceSession = session.fromPartition(`markitdown-${randomUUID()}`, { cache: false });
    const isolated = serviceSession;
    connectionAbort = new AbortController();
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    isolated.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !sameService(details.url, origin) && !details.url.startsWith(`blob:${origin}/`) }));
    // No custom certificate verifier: Chromium's normal TLS checks stay enabled.
    const response = await isolated.fetch(`${origin}/api/config`, { redirect: "error", signal: AbortSignal.any([connectionAbort.signal, AbortSignal.timeout(12000)]) });
    // Electron net.fetch may leave Response.url empty; redirects are refused by
    // fetch and every network request is independently restricted above.
    if (!response.ok || response.redirected) throw new Error("服务未返回可用配置，请核对地址和服务状态。");
    const config = await response.json();
    if (!Array.isArray(config.extensions) || typeof config.has_admin !== "boolean") throw new Error("该地址不是兼容的 MarkItDown 服务。");
    const workspace = await isolated.fetch(`${origin}/app`, { redirect: "error", signal: AbortSignal.any([connectionAbort.signal, AbortSignal.timeout(12000)]) });
    if (!workspace.ok || workspace.redirected || !workspace.headers.get("content-type")?.includes("text/html")) throw new Error("此服务缺少兼容的 /app 工作区，请联系管理员升级服务后重试。");
    if (attempt !== generation) throw new Error("连接已取消。");
    candidate = new BrowserWindow({ width: 1440, height: 960, minWidth: 960, minHeight: 700, backgroundColor: "#f6f7fb", title: "MarkItDown · 文档工作台", show: false,
      webPreferences: { ...preferences, session: isolated } });
    desk = candidate;
    lockContents(candidate.webContents, origin);
    isolated.on("will-download", async (event, item, contents) => {
      if (contents !== desk?.webContents || !allowedDownload(item.getURL(), currentOrigin) || !sameService(contents.getURL(), currentOrigin)) { event.preventDefault(); return; }
      item.setSaveDialogOptions({ title: "保存转换结果", defaultPath: path.join(app.getPath("downloads"), safeFilename(item.getFilename())) });
      item.once("done", (_event, state) => { if (state === "interrupted" && desk && !desk.isDestroyed()) void dialog.showMessageBox(desk, { type: "error", message: "下载中断", detail: "请在工作台重新下载。文件可能已到期或网络连接中断。" }); });
    });
    currentOrigin = origin;
    await candidate.loadURL(`${origin}/app`);
    if (attempt !== generation || candidate.isDestroyed()) throw new Error("连接已取消。");
    lastOrigin = origin;
    await fs.mkdir(app.getPath("userData"), { recursive: true });
    await fs.writeFile(settingsPath(), JSON.stringify({ origin }), { mode: 0o600 });
    candidate.on("closed", () => { desk = null; if (!app.isQuitting) void showSetup(); });
    candidate.webContents.on("render-process-gone", () => { void showSetup("工作区进程已中断，请重新连接。未完成任务请在历史中确认。"); });
    present(candidate);
    setup?.hide();
    return { ok: true };
  } catch (error) {
    if (candidate && !candidate.isDestroyed()) candidate.destroy();
    await disconnect();
    const message = error.message?.includes("ERR_CERT") ? "TLS 证书验证失败。请联系服务管理员修复证书。" : /ERR_|fetch failed|aborted|timeout/i.test(error.message || "") ? "服务暂时不可达，或连接已中断。请确认地址、网络与服务运行状态后重试。" : error.message || "连接失败，请检查服务后重试。";
    return { ok: false, message };
  } finally { connecting = false; }
}

const single = app.requestSingleInstanceLock();
if (!single) app.quit();
else {
  app.on("second-instance", () => { const window = desk || setup; if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    session.fromPartition("markitdown-setup").protocol.handle("markitdown", (request) => {
      const url = new URL(request.url);
      const files = { "/setup.html": "setup.html", "/setup.css": "setup.css", "/setup.js": "setup.js" };
      if (url.host !== "desktop" || !files[url.pathname]) return new Response("Not found", { status: 404 });
      return net.fetch(pathToFileURL(path.join(__dirname, files[url.pathname])).href);
    });
    session.fromPartition("markitdown-setup").setPermissionRequestHandler((_c, _p, callback) => callback(false));
    session.fromPartition("markitdown-setup").setPermissionCheckHandler(() => false);
    try { const saved = JSON.parse(await fs.readFile(settingsPath(), "utf8")); lastOrigin = serviceOrigin(saved.origin); } catch { /* Only a non-secret service address is remembered. */ }
    ipcMain.handle("connection-info", (event) => { if (!trustedSetup(event)) throw new Error("Invalid sender"); return { origin: lastOrigin, version: app.getVersion(), message: startupMessage, distribution: require("../package.json").markitdownDistribution || "unsigned" }; });
    ipcMain.handle("connect-service", (event, value) => { if (!trustedSetup(event)) throw new Error("Invalid sender"); return connect(value); });
    ipcMain.handle("cancel-connection", async (event) => { if (!trustedSetup(event)) throw new Error("Invalid sender"); await disconnect(); return { ok: true }; });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === "darwin" ? [{ label: "MarkItDown", submenu: [{ role: "about" }, { type: "separator" }, { role: "quit" }] }] : []),
      { label: "工作台", submenu: [{ label: "切换服务 / 返回连接页", accelerator: "CmdOrCtrl+Shift+S", click: () => void showSetup("已断开连接并清除客户端会话。服务器任务继续运行；重新登录后可查看历史。") }, { label: "刷新工作区", accelerator: "CmdOrCtrl+R", click: () => desk?.webContents.reload() }, ...(process.platform === "darwin" ? [] : [{ role: "quit" }])] },
      { label: "编辑", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
      { label: "视图", submenu: [{ role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "togglefullscreen" }] }
    ]));
    await showSetup();
  });
  app.on("activate", () => { if (!desk && !setup) void showSetup(); else (desk || setup).show(); });
  app.on("before-quit", () => { app.isQuitting = true; });
  app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
}
