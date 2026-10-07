/*
 * Dependency-free state/DOM regression tests for the real frontend (Node 18+).
 * Run: node packages/markitdown-web/tests/frontend_dom.cjs
 * This DOM double checks behavior, not browser layout, accessibility trees,
 * sanitizer behavior, clipboard permissions, cookie security, or actual downloads.
 * Browser testing is a separate check, not replaced by these simulations.
 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert").strict;
const root = path.resolve(__dirname, "../src/markitdown_web/static");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "app.js"), "utf8");
const NOW = Date.UTC(2026, 9, 5, 12, 0, 0);
const baseConfig = { max_files: 10, max_file_bytes: 20 * 1048576, max_total_bytes: 50 * 1048576, extensions: [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"], retention_seconds: 86400, has_admin: true };
const enhancedConfig = { ...baseConfig, engines: [{ id: "markitdown", available: true, max_file_bytes: 20 * 1048576, timeout_seconds: 45 }, { id: "docling", available: true, max_file_bytes: 10 * 1048576, max_pages: 2, timeout_seconds: 60, ocr: false }] };
const settings = { version: 1, updated_at: NOW / 1000, updated_by: null, defaults: { default_daily_quota: 50, default_max_file_bytes: 20 * 1048576, retention_seconds: 86400 }, current: { default_daily_quota: 50, default_max_file_bytes: 20 * 1048576, retention_seconds: 86400 }, effective: { default_daily_quota: 50, default_max_file_bytes: 20 * 1048576, retention_seconds: 86400 }, bounds: { default_daily_quota: { min: 1, max: 1000 }, default_max_file_bytes: { min: 1048576, max: 20 * 1048576 }, retention_seconds: { min: 3600, max: 604800 } }, deployment: { global_concurrency: 2, max_files: 10 } };
const baseUser = { id: "u1", username: "member", is_admin: false, is_active: true, daily_quota: 20, max_file_bytes: 10 * 1048576 };
const identity = (user = {}) => ({ user: { ...baseUser, ...user }, csrf_token: `test-session-${user.id || "u1"}`, usage: { used: 0, daily_quota: 20, remaining: 20, resets_at: NOW / 1000 + 43200, max_file_bytes: user.max_file_bytes || baseUser.max_file_bytes, active_jobs: 0 } });
const job = (id, status = "succeeded", extra = {}) => ({ id, filename: `file-${id}.md`, status, size_bytes: 40, attempts: 1, created_at: NOW / 1000, expires_at: NOW / 1000 + 86400, error: null, ...extra });
const detail = (item, text = "# Fresh") => ({ ...item, markdown: text, html: `<p>${text}</p>` });
const response = (data, status = 200, userId = "u1") => ({ headers: { get: (name) => name === "X-MarkItDown-User" ? userId : null }, ok: status >= 200 && status < 300, status, json: async () => data, blob: async () => data instanceof Blob ? data : new Blob([String(data)]) });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const flush = async () => { await new Promise((done) => setImmediate(done)); await new Promise((done) => setImmediate(done)); };
const file = (name, size = 12, lastModified = 1) => ({ name, size, lastModified });

class Classes {
  constructor(node) { this.node = node; }
  toggle(name, enabled) { const names = new Set(this.node.className.split(/\s+/).filter(Boolean)); if (enabled ?? !names.has(name)) names.add(name); else names.delete(name); this.node.className = [...names].join(" "); }
  add(name) { this.toggle(name, true); }
  remove(name) { this.toggle(name, false); }
}
class Element {
  constructor(tag, owner) { this.tagName = tag.toUpperCase(); this.owner = owner; this.children = []; this.dataset = {}; this.attributes = {}; this.className = ""; this.classList = new Classes(this); this.listeners = {}; this._text = ""; this._html = ""; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.parent = null; }
  append(...nodes) { for (let node of nodes) { if (typeof node === "string") { const text = new Element("text", this.owner); text.textContent = node; node = text; } if (node.tagName === "FRAGMENT") this.append(...node.children); else { node.parent = this; this.children.push(node); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this._html = ""; this.append(...nodes); }
  set textContent(value) { this._text = String(value); this.children = []; this._html = ""; }
  get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
  set innerHTML(value) { this._html = String(value); this._text = ""; this.children = []; }
  get innerHTML() { return this._html; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(kind, fn) { (this.listeners[kind] ??= []).push(fn); }
  emit(kind, options = {}) { for (const fn of this.listeners[kind] || []) fn({ target: this, preventDefault() {}, stopPropagation() {}, ...options }); }
  select() { this.selected = true; }
  focus() { this.owner.activeElement = this; }
  click() { if (this.disabled) return; if (this.tagName === "A") this.owner.downloads.push({ href: this.href, download: this.download }); this.emit("click"); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this); }
  matches(selector) {
    const tag = selector.match(/^[a-z]+/i)?.[0]; if (tag && this.tagName.toLowerCase() !== tag.toLowerCase()) return false;
    for (const match of selector.matchAll(/\[data-([a-z-]+)(?:="([^"]*)")?\]/g)) { const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()); if (!(key in this.dataset) || (match[2] !== undefined && this.dataset[key] !== match[2])) return false; }
    return true;
  }
  querySelectorAll(selector) { return this.children.flatMap((node) => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
}
function createApp({ config = baseConfig, me = null, jobs = [], usage, routes = {}, clipboardFailure = false, legacyCopy = false } = {}) {
  const document = { activeElement: null, listeners: {}, downloads: [], addEventListener(kind, fn) { this.listeners[kind] = fn; }, createElement(tag) { return new Element(tag, this); }, createDocumentFragment() { return this.createElement("fragment"); } };
  document.body = document.createElement("body"); document.execCommand = () => legacyCopy;
  const elements = {};
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) { const node = document.createElement(match[1]); node.id = match[3]; node.hidden = /\bhidden\b/.test(match[2]); node.disabled = /\bdisabled\b/.test(match[2]); elements[node.id] = node; }
  document.getElementById = (id) => { assert(elements[id], `Unknown HTML id: ${id}`); return elements[id]; };
  const window = { listeners: {}, confirmations: [], confirmResult: true, confirm(text) { this.confirmations.push(text); return this.confirmResult; }, addEventListener(kind, fn) { this.listeners[kind] = fn; } };
  let time = NOW, timerId = 0; const timers = new Map(), urls = new Set(), revoked = [], requests = [], copies = [];
  class FakeDate extends Date { constructor(...args) { super(...(args.length ? args : [time])); } static now() { return time; } }
  class FormData { constructor() { this.parts = []; } append(...values) { this.parts.push(values); } }
  const state = { config, me, jobs, usage: usage || me?.usage || identity().usage, routes };
  const fetch = async (url, options = {}) => {
    assert(url.startsWith("/api/"), `Non same-origin API request: ${url}`); assert.equal(options.credentials, "same-origin"); assert.equal(options.cache, "no-store");
    requests.push({ url, options }); const key = `${options.method || "GET"} ${url}`;
    if (state.routes[key]) return state.routes[key](options, state);
    if (key === "GET /api/admin/settings") return response(settings);
    if (key === "GET /api/admin/audit") return response({ events: [] });
    if (key === "GET /api/config") return response(state.config);
    if (key === "GET /api/me") return state.me ? response(state.me, 200, state.me.user.id) : response({ detail: "Not authenticated" }, 401);
    if (key === "GET /api/jobs") return response({ jobs: state.jobs, usage: state.usage }, 200, state.me?.user.id || "u1");
    if (key === "POST /api/auth/logout") { state.me = null; return response({ status: "ok" }); }
    throw new Error(`Unexpected fetch: ${key}`);
  };
  vm.runInNewContext(script, { document, window, fetch, FormData, Blob, AbortController, TextEncoder, crypto: require("node:crypto").webcrypto, Date: FakeDate, URL: { createObjectURL() { const url = `blob:test-${++timerId}`; urls.add(url); return url; }, revokeObjectURL(url) { urls.delete(url); revoked.push(url); } }, navigator: { clipboard: { writeText: async (text) => { if (clipboardFailure) throw new Error("Permission denied"); copies.push(text); } } }, setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: time + delay, delay }); return id; }, clearTimeout(id) { timers.delete(id); }, console });
  const ui = (id) => elements[id];
  return { state, ui, document, window, timers, urls, revoked, requests, copies,
    async advance(ms) { const target = time + ms; let guard = 0; while (true) { const due = [...timers].filter(([, value]) => value.at <= target).sort((a, b) => a[1].at - b[1].at)[0]; if (!due) break; assert(++guard < 100, "Timer busy loop"); time = due[1].at; timers.delete(due[0]); due[1].fn(); await flush(); } time = target; },
    add(...files) { ui("file-input").files = files; ui("file-input").emit("change"); },
    history(action, index = 0) { const button = ui("history-list").querySelectorAll(`[data-action="${action}"]`)[index]; assert(button, `Missing history action ${action}`); ui("history-list").emit("click", { target: button }); return button; },
    check(index = 0, checked = true) { const input = ui("history-list").querySelectorAll('[data-action="archive"]')[index]; assert(input); input.checked = checked; ui("history-list").emit("change", { target: input }); },
    login(username = "member", password = "test-only-long-password") { ui("login-username").value = username; ui("login-password").value = password; ui("login-form").emit("submit"); },
    calls(path, method) { return requests.filter((entry) => entry.url === path && (!method || entry.options.method === method)); },
    stop() { window.listeners.pagehide(); },
  };
}
const scenarios = [];
async function test(name, fn) { await fn(); scenarios.push(name); }
(async () => {
  await test("first-run bootstrap and config reconnect", async () => {
    const app = createApp({ config: { ...baseConfig, has_admin: false } }); await flush();
    assert(!app.ui("bootstrap-notice").hidden); assert(app.ui("login-button").disabled); assert(app.ui("session-section").hidden); assert(html.includes("markitdown-web bootstrap-admin"));
    app.state.routes["GET /api/config"] = () => response({ detail: "Offline" }, 503); app.ui("reconnect-button").click(); await flush(); assert(!app.ui("connection-notice").hidden); assert(app.ui("choose-button").disabled);
    delete app.state.routes["GET /api/config"]; app.state.config = baseConfig; app.ui("reconnect-button").click(); await flush(); assert(app.ui("bootstrap-notice").hidden); assert(!app.ui("login-button").disabled); app.stop();
  });
  await test("invitation signup, validation, login and duplicate-submit defense", async () => {
    const login = deferred(); const app = createApp({ routes: { "POST /api/auth/register": () => response({ user: baseUser }, 201), "POST /api/auth/login": () => login.promise } }); await flush();
    app.login("UPPER", "short"); assert.equal(app.calls("/api/auth/login").length, 0); assert(app.ui("auth-message").textContent.includes("用户名"));
    app.ui("register-tab").click(); app.ui("register-username").value = "member"; app.ui("register-password").value = "test-only-long-password"; app.ui("invite-token").value = "synthetic-one-use-invite"; app.ui("register-form").emit("submit"); await flush();
    assert.equal(app.ui("register-password").value, ""); assert.equal(app.ui("invite-token").value, ""); assert(!app.ui("login-form").hidden); assert(app.ui("auth-message").textContent.includes("账户已创建")); assert.equal(app.calls("/api/auth/login").length, 0);
    app.login(); app.login(); assert.equal(app.calls("/api/auth/login").length, 1); login.resolve(response(identity())); await flush(); assert(!app.ui("session-section").hidden); assert.equal(app.ui("login-password").value, ""); assert.equal(app.ui("account-name").textContent, "member"); assert(app.ui("admin-toggle").hidden);
    assert.equal(app.calls("/api/auth/register")[0].options.headers["X-MarkItDown-Request"], "1"); assert.equal(app.calls("/api/auth/login")[0].options.headers["X-CSRF-Token"], undefined); app.stop();
  });
  await test("server/user limits, filenames as text, dedupe and quota", async () => {
    const me = identity({ max_file_bytes: 100 }); me.usage.remaining = 1;
    const app = createApp({ me, config: { ...baseConfig, max_files: 3, max_file_bytes: 200, max_total_bytes: 150, extensions: [".md", ".txt"] } }); await flush();
    assert.equal(app.ui("file-input").accept, ".md,.txt"); assert(app.ui("upload-limits").textContent.includes("单个 100 B")); assert(app.ui("quota-reset").textContent.includes("UTC"));
    app.add(file("<img onerror=1>.md", 80), file("overflow.md", 80), file("bad.exe", 1)); assert.equal(app.ui("queue-count").textContent, "3"); assert.equal(app.ui("file-list").children[1].dataset.state, "error"); assert.equal(app.ui("file-list").children[2].dataset.state, "error"); assert.equal(app.ui("file-list").children[0].innerHTML, "");
    app.add(file("fourth.txt")); assert(app.ui("notice").textContent.includes("上限")); app.ui("clear-button").click();
    app.add(file("empty.txt", 0), file("large.txt", 101)); assert(app.ui("convert-button").disabled); app.ui("clear-button").click();
    app.add(file("same.txt", 10)); app.add(file("same.txt", 10)); assert.equal(app.ui("queue-count").textContent, "1"); assert(app.ui("notice").textContent.includes("重复"));
    app.add(file("two.md", 10)); app.ui("convert-button").click(); assert.equal(app.calls("/api/jobs", "POST").length, 0); assert(app.ui("notice").textContent.includes("还可上传 1")); app.stop();
  });
  await test("queued upload, partial errors, polling, success and preview tabs", async () => {
    const queued = job("a", "queued"), upload = deferred();
    const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => upload.promise, "GET /api/jobs/a": () => response(detail(job("a"), "# Result")) } }); await flush();
    app.add(file("one.md"), file("two.md")); app.ui("convert-button").click(); app.ui("convert-button").click(); assert.equal(app.calls("/api/jobs", "POST").length, 1); assert(app.ui("choose-button").disabled);
    const request = app.calls("/api/jobs", "POST")[0]; assert.equal(request.options.body.parts.length, 3); assert.equal(request.options.body.parts.filter((part) => part[0] === "files").length, 2); assert.equal(request.options.body.parts.find((part) => part[0] === "engine")[1], "markitdown"); assert.equal(request.options.headers["X-CSRF-Token"], identity().csrf_token);
    app.state.jobs = [queued]; upload.resolve(response({ jobs: [queued], errors: [{ filename: "<hostile>.md", error: "<img onerror=1>" }] }, 202)); await flush();
    assert.equal(app.ui("queue-count").textContent, "0"); assert.equal(app.ui("history-list").children[0].dataset.state, "queued"); assert(app.ui("notice").textContent.includes("<img onerror=1>")); assert.equal(app.ui("notice").innerHTML, ""); assert([...app.timers.values()].some((timer) => timer.delay === 2000));
    app.state.jobs = [job("a", "running")]; await app.advance(2000); assert.equal(app.ui("history-list").children[0].dataset.state, "running");
    app.state.jobs = [job("a")]; await app.advance(2000); assert.equal(app.ui("markdown-preview").innerHTML, "<p># Result</p>"); assert(![...app.timers.values()].some((timer) => timer.delay === 2000));
    app.ui("source-tab").click(); assert(!app.ui("source-panel").hidden); assert.equal(app.ui("markdown-source").value, "# Result"); app.ui("source-tab").emit("keydown", { key: "Home" }); assert.equal(app.ui("preview-tab").getAttribute("aria-selected"), "true");
    app.ui("copy-button").click(); await flush(); assert.equal(app.copies[0], "# Result"); app.ui("download-button").click(); assert.equal(app.document.downloads[0].href, "/api/jobs/a/download"); assert.equal(app.document.body.children.length, 0); app.stop();
  });
  await test("selection response race and empty successful result", async () => {
    const old = deferred(); const app = createApp({ me: identity(), jobs: [job("a"), job("b")], routes: { "GET /api/jobs/a": () => old.promise, "GET /api/jobs/b": () => response(detail(job("b"), "")) } }); await flush();
    app.history("select", 0); app.history("select", 1); await flush(); assert.equal(app.ui("empty-title").textContent, "没有可预览的文字"); assert(!app.ui("download-button").disabled);
    old.resolve(response(detail(job("a"), "STALE"))); await flush(); assert.equal(app.ui("markdown-source").value, ""); assert.equal(app.ui("document-name").textContent, "file-b.md"); app.stop();
  });
  await test("retry, explicit deletion confirmation and status failures", async () => {
    const failed = job("bad", "failed", { error: "Broken <script>" }); const retry = deferred();
    const app = createApp({ me: identity(), jobs: [failed], routes: { "POST /api/jobs/bad/retry": () => retry.promise, "DELETE /api/jobs/bad": (_, state) => { state.jobs = []; return response({ status: "deleted" }); } } }); await flush();
    app.history("select"); assert.equal(app.ui("empty-description").textContent, failed.error); app.history("retry"); app.history("retry"); assert.equal(app.calls("/api/jobs/bad/retry").length, 1);
    app.state.jobs = [job("bad", "queued", { attempts: 2 })]; retry.resolve(response(app.state.jobs[0])); await flush(); assert.equal(app.ui("history-list").children[0].dataset.state, "queued");
    app.window.confirmResult = false; app.history("delete"); assert.equal(app.calls("/api/jobs/bad", "DELETE").length, 0); app.window.confirmResult = true; app.history("delete"); await flush(); assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.ui("markdown-source").value, ""); assert(app.window.confirmations[0].includes("无法恢复")); app.stop();
  });
  await test("24-hour expiry removes preview, ZIP eligibility and retry", async () => {
    const item = job("soon", "succeeded", { expires_at: NOW / 1000 + 1 }); const app = createApp({ me: identity(), jobs: [item], routes: { "GET /api/jobs/soon": () => response(detail(item)) } }); await flush();
    app.history("select"); await flush(); app.check(); assert(!app.ui("archive-button").disabled); await app.advance(1100);
    assert.equal(app.ui("history-list").children[0].dataset.state, "expired"); assert(app.ui("copy-button").disabled); assert(app.ui("download-button").disabled); assert(app.ui("archive-button").disabled); assert.equal(app.ui("markdown-preview").innerHTML, ""); assert.equal(app.ui("empty-title").textContent, "文件已过期"); assert.equal(app.ui("history-list").querySelectorAll('[data-action="retry"]').length, 0); app.stop();
  });
  await test("ZIP selection limit, repeated click and object URL cleanup", async () => {
    const zip = deferred(); const app = createApp({ me: identity(), jobs: Array.from({ length: 12 }, (_, i) => job(String(i))), routes: { "POST /api/jobs/archive": () => zip.promise } }); await flush();
    for (let i = 0; i < 11; i++) app.check(i); assert(app.ui("archive-count").textContent.includes("10 / 10")); assert(app.ui("notice").textContent.includes("最多选择 10"));
    app.ui("archive-button").click(); app.ui("archive-button").click(); assert.equal(app.calls("/api/jobs/archive").length, 1); assert.equal(JSON.parse(app.calls("/api/jobs/archive")[0].options.body).job_ids.length, 10);
    zip.resolve(response(new Blob(["ZIP"]))); await flush(); assert.equal(app.urls.size, 1); assert.equal(app.document.body.children.length, 0); assert.equal(app.document.downloads[0].download, "markitdown-results.zip"); await app.advance(1000); assert.equal(app.urls.size, 0); app.stop();
  });
  await test("logout clears files, preview and stale cross-user responses", async () => {
    const old = deferred(), upload = deferred(), list = deferred(); const app = createApp({ me: identity(), jobs: [job("private")], routes: { "GET /api/jobs/private": () => old.promise, "POST /api/jobs": () => upload.promise, "POST /api/auth/login": (_, state) => { state.me = identity({ id: "u2", username: "other" }); return response(state.me, 200, "u2"); } } }); await flush();
    app.history("select"); app.add(file("secret.txt")); app.ui("convert-button").click(); app.state.routes["GET /api/jobs"] = () => list.promise; app.ui("refresh-button").click(); app.ui("logout-button").click();
    assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.ui("queue-count").textContent, "0"); assert.equal(app.ui("file-input").value, ""); assert.equal(app.ui("markdown-preview").innerHTML, ""); assert(app.ui("session-section").hidden); await flush();
    delete app.state.routes["GET /api/jobs"]; app.state.jobs = []; app.login("other"); await flush(); assert.equal(app.ui("account-name").textContent, "other");
    old.resolve(response(detail(job("private"), "SECRET"))); upload.resolve(response({ jobs: [job("leaked")], errors: [] }, 202)); list.resolve(response({ jobs: [job("leaked")], usage: identity().usage })); await flush();
    assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.ui("markdown-preview").innerHTML, ""); assert.equal(app.ui("account-name").textContent, "other"); assert.equal(app.calls("/api/auth/logout")[0].options.headers["X-CSRF-Token"], identity().csrf_token); app.stop();
  });
  await test("failed logout blocks login until server confirms retry", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/auth/logout": () => response({ detail: "Offline" }, 503) } }); await flush(); app.ui("logout-button").click(); await flush();
    assert(app.ui("session-section").hidden); assert(!app.ui("logout-retry").hidden); assert(app.ui("login-button").disabled); assert(app.ui("auth-message").textContent.includes("尚未确认退出"));
    delete app.state.routes["POST /api/auth/logout"]; app.ui("logout-retry").click(); await flush(); assert(app.ui("logout-retry").hidden); assert(!app.ui("login-button").disabled); app.stop();
  });
  await test("401 invalidates session and 403/409/429 stay recoverable", async () => {
    const app = createApp({ me: identity(), jobs: [job("f", "failed")] }); await flush();
    for (const status of [403, 409, 429]) { app.state.routes["POST /api/jobs/f/retry"] = () => response({ detail: `Server ${status}` }, status); app.history("retry"); await flush(); assert(app.ui("notice").textContent.includes(String(status))); assert(!app.ui("session-section").hidden); }
    app.state.routes["GET /api/jobs"] = () => response({ detail: "Expired" }, 401); app.ui("refresh-button").click(); await flush(); assert(app.ui("session-section").hidden); assert.equal(app.ui("history-count").textContent, "0"); assert(app.ui("auth-message").textContent.includes("登录已失效")); app.stop();
  });
  await test("admin-only invites, user limits, validation and cleanup", async () => {
    const admin = identity({ is_admin: true }); const invites = [{ id: 1, expires_at: NOW / 1000 + 86400, used_at: null, revoked_at: null }];
    const app = createApp({ me: admin, routes: { "GET /api/admin/invites": () => response({ invites }), "GET /api/admin/users": () => response({ users: [{ ...baseUser, id: "u2", username: "other" }] }), "POST /api/admin/invites": () => response({ id: 1, token: "synthetic-ephemeral-token", expires_at: NOW / 1000 + 86400 }), "DELETE /api/admin/invites/1": () => response({ status: "revoked" }), "PATCH /api/admin/users/u2": () => response({ user: baseUser }) } }); await flush();
    assert(!app.ui("admin-toggle").hidden); app.ui("admin-toggle").click(); await flush(); assert.equal(app.ui("users-list").children.length, 1);
    app.ui("invite-create").click(); await flush(); assert.equal(app.ui("new-invite-token").value, "synthetic-ephemeral-token"); assert.equal(JSON.parse(app.calls("/api/admin/invites", "POST")[0].options.body).ttl_hours, 24); app.ui("invite-copy").click(); await flush(); assert.equal(app.copies[0], "synthetic-ephemeral-token");
    const form = app.ui("users-list").querySelector("form"), fields = form.querySelectorAll("[data-field]"); fields.find((input) => input.dataset.field === "daily_quota").value = "1001"; app.ui("users-list").emit("submit", { target: form }); await flush(); assert.equal(app.calls("/api/admin/users/u2").length, 0);
    fields.find((input) => input.dataset.field === "daily_quota").value = "100"; fields.find((input) => input.dataset.field === "max_file_mib").value = "2"; fields.find((input) => input.dataset.field === "is_active").checked = false;
    app.ui("users-list").emit("submit", { target: form }); await flush(); assert.deepEqual(JSON.parse(app.calls("/api/admin/users/u2")[0].options.body), { daily_quota: 100, max_file_bytes: 2 * 1048576, is_active: false });
    const revoke = app.ui("invites-list").querySelector('[data-action="revoke"]'); app.ui("invites-list").emit("click", { target: revoke }); await flush(); assert.equal(app.calls("/api/admin/invites/1", "DELETE").length, 1); assert.equal(app.ui("new-invite-token").value, "");
    app.ui("invite-create").click(); await flush(); app.ui("logout-button").click(); await flush(); assert(app.ui("admin-panel").hidden); assert.equal(app.ui("new-invite-token").value, ""); assert.equal(app.ui("users-list").children.length, 0); assert.equal(app.ui("invites-list").children.length, 0); app.stop();
  });
  await test("admin request completing after logout cannot restore invitations", async () => {
    const invite = deferred(); const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "POST /api/admin/invites": () => invite.promise } }); await flush(); app.ui("admin-toggle").click(); await flush(); app.ui("invite-create").click(); app.ui("logout-button").click(); await flush(); invite.resolve(response({ token: "LATE", expires_at: NOW / 1000 + 1 })); await flush(); assert.equal(app.ui("new-invite-token").value, ""); assert(app.ui("invite-result").hidden); assert(!app.ui("invite-create").disabled); app.stop();
  });
  await test("navigation clears retained state and back-forward revalidates", async () => {
    const app = createApp({ me: identity(), jobs: [job("a")], routes: { "GET /api/jobs/a": () => response(detail(job("a"))) } }); await flush(); app.history("select"); await flush(); app.add(file("local.md")); app.window.listeners.pagehide(); assert.equal(app.ui("markdown-source").value, ""); assert.equal(app.ui("queue-count").textContent, "0"); assert.equal(app.timers.size, 0);
    app.state.me = null; app.window.listeners.pageshow({ persisted: true }); await flush(); assert(app.ui("session-section").hidden); assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.calls("/api/me").length, 2); app.stop();
  });
  await test("late ZIP response after logout creates no download", async () => {
    const zip = deferred(); const app = createApp({ me: identity(), jobs: [job("a")], routes: { "POST /api/jobs/archive": () => zip.promise } }); await flush(); app.check(); app.ui("archive-button").click(); app.ui("logout-button").click(); await flush(); zip.resolve(response(new Blob(["STALE ZIP"]))); await flush(); assert.equal(app.urls.size, 0); assert.equal(app.document.downloads.length, 0); app.stop();
  });
  await test("unprivileged DOM tampering never enables admin API access", async () => {
    const app = createApp({ me: identity() }); await flush(); app.ui("admin-toggle").hidden = false; app.ui("admin-toggle").click(); app.ui("invite-create").click(); app.ui("admin-refresh").click(); await flush(); assert(!app.requests.some((request) => request.url.startsWith("/api/admin"))); app.stop();
  });
  await test("cross-tab identity headers clear old account before painting", async () => {
    const app = createApp({ me: identity(), jobs: [job("private")], routes: { "GET /api/jobs/private": () => response(detail(job("private"))) } }); await flush();
    app.history("select"); await flush(); app.add(file("private.md"));
    app.state.routes["GET /api/jobs"] = () => response({ jobs: [job("other-account")], usage: identity().usage }, 200, "u2");
    app.ui("refresh-button").click(); await flush(); assert(app.ui("session-section").hidden); assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.ui("markdown-preview").innerHTML, ""); assert.equal(app.ui("queue-count").textContent, "0"); assert(!app.ui("connection-notice").hidden); assert(app.ui("login-button").disabled);
    app.state.me = identity({ id: "u2", username: "other" }); app.state.jobs = []; delete app.state.routes["GET /api/jobs"]; app.ui("reconnect-button").click(); await flush(); assert.equal(app.ui("account-name").textContent, "other"); assert(!app.ui("session-section").hidden); app.stop();
  });
  await test("cross-tab logout rejects stale CSRF and offers account reconnect", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/auth/logout": () => response({ detail: "CSRF mismatch" }, 403, "u2") } }); await flush(); app.ui("logout-button").click(); await flush(); assert(app.ui("session-section").hidden); assert(app.ui("logout-retry").hidden); assert(!app.ui("connection-notice").hidden); assert(app.ui("connection-message").textContent.includes("切换账户")); app.stop();
  });
  await test("authenticated successful responses without identity header are refused", async () => {
    const app = createApp({ me: identity(), routes: { "GET /api/jobs": () => response({ jobs: [job("unverified")], usage: identity().usage }, 200, null) } }); await flush(); assert(app.ui("session-section").hidden); assert.equal(app.ui("history-count").textContent, "0"); assert(!app.ui("connection-notice").hidden); app.stop();
  });
  await test("refresh predating completed upload cannot overwrite newer jobs", async () => {
    const upload = deferred(), oldList = deferred(); const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => upload.promise } }); await flush();
    app.add(file("new.md")); app.ui("convert-button").click(); app.state.routes["GET /api/jobs"] = () => oldList.promise; app.ui("refresh-button").click();
    app.state.jobs = [job("new", "queued")]; delete app.state.routes["GET /api/jobs"]; upload.resolve(response({ jobs: app.state.jobs, errors: [] }, 202)); await flush(); assert.equal(app.ui("history-count").textContent, "1");
    oldList.resolve(response({ jobs: [], usage: identity().usage })); await flush(); assert.equal(app.ui("history-count").textContent, "1"); assert.equal(app.ui("history-list").children[0].dataset.state, "queued"); app.stop();
  });
  await test("focus revalidates account and user limits without restoring stale data", async () => {
    const app = createApp({ me: identity() }); await flush(); app.add(file("within-old-limit.md", 400)); app.state.me = identity({ max_file_bytes: 100 }); app.window.listeners.focus(); await flush(); assert.equal(app.ui("file-list").children[0].dataset.state, "error"); assert(app.ui("convert-button").disabled);
    app.state.me = identity({ id: "u2", username: "other" }); app.window.listeners.focus(); await flush(); assert(app.ui("session-section").hidden); assert.equal(app.ui("queue-count").textContent, "0"); app.stop();
  });
  await test("retry quota and three-attempt lifetime cap are visible", async () => {
    const app = createApp({ me: identity(), jobs: [job("maxed", "failed", { attempts: 3 })] }); await flush(); const retry = app.ui("history-list").querySelector('[data-action="retry"]'); assert(retry.disabled); assert(retry.textContent.includes("次数已用完")); app.history("retry"); assert.equal(app.calls("/api/jobs/maxed/retry").length, 0); assert(app.ui("conversion-summary").textContent.includes("重试")); app.stop();
  });
  await test("interrupted initial connection resumes after back-forward navigation", async () => {
    const config = deferred(); const app = createApp({ routes: { "GET /api/config": () => config.promise } });
    app.window.listeners.pagehide(); delete app.state.routes["GET /api/config"]; app.window.listeners.pageshow({ persisted: true }); await flush(); assert(!app.ui("login-button").disabled);
    config.resolve(response({ ...baseConfig, has_admin: false })); await flush(); assert(app.ui("bootstrap-notice").hidden); assert(!app.ui("login-button").disabled); app.stop();
  });
  await test("legacy config defaults safely and unavailable engine cannot be forced in DOM", async () => {
    const app = createApp({ me: identity() }); await flush();
    assert(app.ui("engine-markitdown").checked); assert(app.ui("engine-docling").disabled);
    assert(app.ui("engine-limit-docling").textContent.includes("尚未启用"));
    app.ui("engine-docling").disabled = false; app.ui("engine-docling").emit("change");
    assert(app.ui("engine-markitdown").checked); assert(app.ui("engine-docling").disabled);
    app.stop();
  });
  await test("engine availability reasons are text and unavailable default blocks uploads", async () => {
    const app = createApp({ me: identity(), config: { ...baseConfig, engines: [{ id: "markitdown", available: false, reason: "<img onerror=1>" }, { id: "docling", available: false, reason: "Missing local models" }] } }); await flush();
    app.add(file("safe.md")); assert(app.ui("convert-button").disabled);
    assert.equal(app.ui("engine-selection-note").textContent, "<img onerror=1>"); assert.equal(app.ui("engine-selection-note").innerHTML, "");
    assert(app.ui("engine-limit-docling").textContent.includes("Missing local models")); app.stop();
  });
  await test("Docling preflight revalidates PDF-only and the stricter engine or user size cap", async () => {
    const app = createApp({ me: identity({ max_file_bytes: 20 * 1048576 }), config: enhancedConfig }); await flush();
    app.add(file("notes.md"), file("too-large.pdf", 11 * 1048576));
    app.ui("engine-docling").emit("change"); assert.equal(app.ui("file-input").accept, ".pdf");
    assert(app.ui("convert-button").disabled); assert(app.ui("file-list").children[0].textContent.includes("仅支持 PDF"));
    assert(app.ui("file-list").children[1].textContent.includes("10 MiB"));
    assert(app.ui("engine-selection-note").textContent.includes("2 页会拒绝"));
    assert(app.ui("engine-limit-docling").textContent.includes("60 秒")); assert(app.ui("engine-limit-docling").textContent.includes("OCR 关闭"));
    app.ui("engine-markitdown").emit("change"); assert(!app.ui("convert-button").disabled);
    app.ui("clear-button").click(); app.state.me = identity({ max_file_bytes: 1024 }); app.window.listeners.focus(); await flush();
    app.ui("engine-docling").emit("change"); app.add(file("user-limit.pdf", 2048)); assert(app.ui("convert-button").disabled);
    assert(app.ui("upload-limits").textContent.includes("单个 1 KiB")); app.stop();
  });
  await test("upload captures the chosen engine and freezes selection until server acceptance", async () => {
    const upload = deferred(); const item = job("enhanced", "queued", { filename: "report.pdf", engine: "docling" });
    const app = createApp({ me: identity(), config: enhancedConfig, routes: { "POST /api/jobs": () => upload.promise } }); await flush();
    app.ui("engine-docling").emit("change"); app.add(file("report.pdf")); app.ui("convert-button").click();
    assert(app.ui("engine-markitdown").disabled); assert(!app.ui("upload-progress").hidden);
    app.ui("engine-markitdown").emit("change"); assert(app.ui("engine-docling").checked);
    assert.equal(app.calls("/api/jobs", "POST")[0].options.body.parts.find(([key]) => key === "engine")[1], "docling");
    app.state.jobs = [item]; upload.resolve(response({ jobs: [item], errors: [] }, 202)); await flush();
    assert(app.ui("upload-progress").hidden); assert(!app.ui("engine-markitdown").disabled);
    assert(app.ui("history-list").textContent.includes("Docling")); assert.equal(app.ui("document-engine").textContent, "Docling"); app.stop();
  });
  await test("cancel is idempotent in flight, exposes cancelled state and preserves retry engine", async () => {
    const cancellation = deferred(); const item = job("cancel", "running", { filename: "report.pdf", engine: "docling" });
    const app = createApp({ me: identity(), config: enhancedConfig, jobs: [item], routes: { "POST /api/jobs/cancel/cancel": () => cancellation.promise } }); await flush();
    app.history("select"); assert(!app.ui("document-cancel").hidden); app.ui("document-cancel").click(); app.history("cancel");
    assert.equal(app.calls("/api/jobs/cancel/cancel").length, 1);
    app.state.jobs = [job("cancel", "failed", { filename: "report.pdf", engine: "docling", error: "任务已取消" })]; cancellation.resolve(response(app.state.jobs[0])); await flush();
    assert.equal(app.ui("output-label").textContent, "已取消"); assert(app.ui("document-progress").hidden); assert(app.ui("document-cancel").hidden); assert(!app.ui("document-retry").hidden);
    assert(app.ui("empty-description").textContent.includes("额度不退还"));
    app.state.routes["POST /api/jobs/cancel/retry"] = () => response({ detail: "任务正在停止，请稍后重试" }, 409);
    app.ui("document-retry").click(); await flush(); assert(app.ui("notice").textContent.includes("正在停止"));
    app.state.routes["POST /api/jobs/cancel/retry"] = (_, state) => { state.jobs = [job("cancel", "queued", { engine: "docling", attempts: 2 })]; return response(state.jobs[0]); };
    app.ui("document-retry").click(); await flush();
    assert.equal(app.calls("/api/jobs/cancel/retry")[1].options.body, undefined); assert.equal(app.ui("document-engine").textContent, "Docling"); app.stop();
  });
  await test("late cancellation cannot repopulate private document state after logout", async () => {
    const cancellation = deferred(); const app = createApp({ me: identity(), config: enhancedConfig, jobs: [job("private", "running", { engine: "docling" })], routes: { "POST /api/jobs/private/cancel": () => cancellation.promise } }); await flush();
    app.history("select"); app.history("cancel"); app.ui("logout-button").click(); await flush();
    cancellation.resolve(response(job("private", "failed", { engine: "docling", error: "任务已取消" }))); await flush();
    assert.equal(app.ui("history-count").textContent, "0"); assert.equal(app.ui("document-engine").textContent, ""); assert(app.ui("engine-markitdown").checked); assert(app.ui("document-progress").hidden); app.stop();
  });
  await test("unknown history action cannot fall through to deletion", async () => {
    const app = createApp({ me: identity(), jobs: [job("safe")] }); await flush();
    const button = app.ui("history-list").querySelector('[data-action="delete"]'); button.dataset.action = "unexpected";
    app.ui("history-list").emit("click", { target: button }); await flush(); assert.equal(app.calls("/api/jobs/safe", "DELETE").length, 0); assert.equal(app.window.confirmations.length, 0); app.stop();
  });
  await test("history filters keep counts and cross-filter ZIP selection without hiding outcomes", async () => {
    const items = [job("active", "running"), job("ready"), job("problem", "failed"), job("expired", "succeeded", { expires_at: NOW / 1000 - 1 })];
    const app = createApp({ me: identity(), jobs: items }); await flush();
    assert(app.ui("workspace-status").textContent.includes("1 项处理中 · 1 项已完成 · 2 项需处理"));
    app.ui("filter-completed").click(); assert.equal(app.ui("history-list").children.length, 1); app.check();
    app.ui("filter-active").click(); assert.equal(app.ui("history-list").children.length, 1); assert(!app.ui("archive-button").disabled);
    app.ui("filter-failed").click(); assert.equal(app.ui("history-list").children.length, 2); assert.equal(app.ui("filter-failed").getAttribute("aria-pressed"), "true");
    app.state.jobs = [job("ready")]; app.ui("refresh-button").click(); await flush(); assert(!app.ui("history-empty").hidden); assert(app.ui("history-empty").textContent.includes("此分类")); app.stop();
  });
  await test("processing is indeterminate and upload/read busy states clear after completion", async () => {
    const reading = deferred(); const app = createApp({ me: identity(), jobs: [job("read")], routes: { "GET /api/jobs/read": () => reading.promise } }); await flush();
    app.history("select"); assert(!app.ui("document-progress").hidden); assert.equal(app.ui("output-content").getAttribute("aria-busy"), "true");
    reading.resolve(response(detail(job("read")))); await flush(); assert(app.ui("document-progress").hidden); assert.equal(app.ui("output-content").getAttribute("aria-busy"), "false");
    const progressTags = [...html.matchAll(/<progress\b[^>]*>/g)].map((match) => match[0]); assert.equal(progressTags.length, 2);
    assert(progressTags.every((tag) => !/\bvalue=/.test(tag))); assert(!script.includes("aria-valuenow")); app.stop();
  });
  await test("three view modes support keyboard wraparound, source fidelity and page metadata", async () => {
    const item = job("pdf", "succeeded", { filename: "table.pdf", engine: "docling", metadata: { page_count: 2 } });
    const source = "# Heading\n\n| A | B |\n| - | - |\n| 1 | 2 |";
    const app = createApp({ me: identity(), config: enhancedConfig, jobs: [item], routes: { "GET /api/jobs/pdf": () => response(detail(item, source)) } }); await flush(); app.history("select"); await flush();
    app.ui("preview-tab").emit("keydown", { key: "ArrowLeft" }); assert.equal(app.document.activeElement, app.ui("split-tab"));
    assert(!app.ui("preview-panel").hidden && !app.ui("source-panel").hidden); assert(app.ui("output-content").className.includes("is-split"));
    assert.equal(app.ui("source-panel").getAttribute("aria-labelledby"), "split-tab"); assert.equal(app.ui("markdown-source").value, source);
    assert.equal(app.ui("document-engine").textContent, "Docling · 2 页"); assert(!app.ui("preview-safety").hidden);
    app.ui("split-tab").emit("keydown", { key: "ArrowRight" }); assert.equal(app.document.activeElement, app.ui("preview-tab")); assert(app.ui("source-panel").hidden);
    app.ui("preview-tab").emit("keydown", { key: "End" }); assert.equal(app.ui("split-tab").getAttribute("aria-selected"), "true");
    app.ui("source-tab").click(); app.ui("copy-button").click(); await flush(); assert.equal(app.copies[0], source); assert(app.ui("copy-button").textContent.includes("已复制")); app.stop();
  });
  await test("failed result read has an explicit reload action and clears its previous error", async () => {
    const app = createApp({ me: identity(), jobs: [job("read")], routes: { "GET /api/jobs/read": () => response({ detail: "Temporary reader failure" }, 503) } }); await flush(); app.history("select"); await flush();
    assert(!app.ui("document-reload").hidden); assert(app.ui("download-button").disabled);
    app.state.routes["GET /api/jobs/read"] = () => response(detail(job("read"), "Recovered")); app.ui("document-reload").click(); await flush();
    assert(app.ui("document-reload").hidden); assert.equal(app.ui("markdown-source").value, "Recovered"); assert(!app.ui("download-button").disabled); app.stop();
  });
  await test("ambiguous upload failure freezes payload and safe retry reuses the submission key", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => response({ detail: "Upload response interrupted" }, 503) } }); await flush();
    app.add(file("uncertain.md")); app.ui("convert-button").click(); await flush();
    assert(app.ui("convert-button").disabled); assert(!app.ui("upload-recheck").hidden); assert(app.ui("notice").textContent.includes("核对转换记录"));
    app.ui("convert-button").emit("click"); assert.equal(app.calls("/api/jobs", "POST").length, 1);
    app.ui("upload-recheck").click(); await flush(); assert(app.ui("convert-button").disabled); assert.equal(app.calls("/api/jobs", "POST").length, 2); assert.equal(app.calls("/api/jobs", "POST")[0].options.headers["Idempotency-Key"], app.calls("/api/jobs", "POST")[1].options.headers["Idempotency-Key"]);
    app.ui("clear-button").click(); assert(app.ui("upload-recheck").hidden); assert.equal(app.ui("queue-count").textContent, "0"); app.stop();
  });
  await test("partial server rejection keeps the rejected file and can be reconsidered with another engine", async () => {
    const app = createApp({ me: identity(), config: enhancedConfig, routes: { "POST /api/jobs": (_, state) => { state.jobs = [job("accepted", "queued", { filename: "good.pdf", engine: "docling" })]; return response({ jobs: state.jobs, errors: [{ filename: "too-many-pages.pdf", error: "PDF 超过 2 页" }] }, 202); } } }); await flush();
    app.ui("engine-docling").emit("change"); app.add(file("good.pdf"), file("too-many-pages.pdf")); app.ui("convert-button").click(); await flush();
    assert.equal(app.ui("queue-count").textContent, "1"); assert(app.ui("file-list").textContent.includes("PDF 超过 2 页")); assert(app.ui("convert-button").disabled);
    app.ui("refresh-button").click(); await flush(); assert(app.ui("convert-button").disabled);
    app.ui("engine-markitdown").emit("change"); assert(!app.ui("convert-button").disabled); app.stop();
  });
  await test("keyboard focus survives ZIP selection and removing the last queued file", async () => {
    const app = createApp({ me: identity(), jobs: [job("focus")] }); await flush();
    const checkbox = app.ui("history-list").querySelector('[data-action="archive"]'); checkbox.focus(); app.check();
    assert.equal(app.document.activeElement.dataset.action, "archive"); assert(app.document.activeElement.checked);
    app.add(file("remove.md")); const remove = app.ui("file-list").querySelector('[data-action="remove"]'); remove.focus(); app.ui("file-list").emit("click", { target: remove });
    assert.equal(app.document.activeElement, app.ui("choose-button")); app.stop();
  });
  await test("drag/drop accepts files, clears nested drag highlight and ignores busy drops", async () => {
    const upload = deferred(); const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => upload.promise } }); await flush();
    app.ui("drop-zone").emit("dragenter"); app.ui("drop-zone").emit("dragenter"); app.ui("drop-zone").emit("dragleave"); assert(app.ui("drop-zone").className.includes("drag-over"));
    app.ui("drop-zone").emit("drop", { dataTransfer: { files: [file("drop.md")] } }); assert(!app.ui("drop-zone").className.includes("drag-over")); assert.equal(app.ui("queue-count").textContent, "1");
    app.ui("convert-button").click(); app.ui("drop-zone").emit("drop", { dataTransfer: { files: [file("ignored.md")] } }); assert.equal(app.ui("queue-count").textContent, "1");
    assert.equal(app.ui("drop-zone").getAttribute("aria-disabled"), "true"); app.stop(); upload.resolve(response({ jobs: [], errors: [] }, 202)); await flush();
  });
  await test("HTML and CSS provide native engine semantics, mobile targets and reduced-motion paths", async () => {
    assert(/<fieldset[^>]*id="engine-group"/.test(html)); assert(/<legend>选择转换引擎<\/legend>/.test(html));
    assert(/id="engine-docling"[^>]*type="radio"[^>]*name="engine"/.test(html));
    assert(/id="engine-docling"[^>]*aria-describedby="engine-description-docling engine-limit-docling"/.test(html));
    assert(/href="#output-heading"/.test(html)); assert(/id="output-heading" tabindex="-1"/.test(html));
    const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
    assert(/@media\s*\(max-width:\s*520px\)/.test(css)); assert(/min-height:\s*44px/.test(css));
    assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)/.test(css)); assert(/@media\s*\(forced-colors:\s*active\)/.test(css));
    assert(css.includes(".output-content.is-split")); assert(!css.includes("@import"));
  });
  await test("six-character authentication boundary is shared by native fields and Unicode-aware JavaScript", async () => {
    assert.equal((html.match(/minlength="6"/g) || []).length, 2); assert(!html.includes('minlength="12"'));
    const app = createApp({ routes: { "POST /api/auth/login": () => response({ detail: "Synthetic rejection" }, 400) } }); await flush();
    app.login("member", "12345"); await flush(); assert.equal(app.calls("/api/auth/login").length, 0); assert(app.ui("auth-message").textContent.includes("6–128"));
    app.login("member", "123456"); await flush(); assert.equal(app.calls("/api/auth/login").length, 1);
    app.login("member", "😀😀😀"); await flush(); assert.equal(app.calls("/api/auth/login").length, 1);
    app.login("member", "😀😀😀😀😀😀"); await flush(); assert.equal(app.calls("/api/auth/login").length, 2); app.stop();
  });
  await test("used expired revoked and invalid invitations expose actionable states without account discovery", async () => {
    const app = createApp(); await flush(); app.ui("register-tab").click(); app.ui("register-username").value = "member"; app.ui("register-password").value = "123456"; app.ui("invite-token").value = "synthetic-only";
    for (const [code, label] of [["invite_expired", "过期"], ["invite_used", "已使用"], ["invite_revoked", "撤销"], ["invite_invalid", "无效"]]) {
      app.state.routes["POST /api/auth/register"] = () => response({ detail: "Invitation unavailable", code }, 400);
      app.ui("register-form").emit("submit"); await flush(); assert(app.ui("auth-message").textContent.includes(label)); assert(app.ui("auth-message").textContent.includes("管理员"));
    } app.stop();
  });
  await test("retention shows real configured duration admission clock and local quota reset", async () => {
    const app = createApp({ me: identity(), config: { ...baseConfig, retention_seconds: 7200, history_seconds: 2592000 } }); await flush();
    assert(app.ui("auth-retention").textContent.includes("入队起 2 小时")); assert(app.ui("account-retention").textContent.includes("入队起 2 小时"));
    assert(app.ui("quota-reset").textContent.includes("UTC 00:00")); assert(app.ui("quota-reset").textContent.includes("本地"));
    assert(app.ui("history-note").textContent.includes("30 天")); assert(!html.includes("从上传起保留 24 小时"));
    assert(app.ui("engine-limit-markitdown").textContent.includes("无 2 页硬门槛")); assert(app.ui("engine-limit-markitdown").textContent.includes("OCR 关闭")); app.stop();
  });
  await test("safe upload replay preserves bytes engine and key after a lost acceptance response", async () => {
    let accepted = false;
    const app = createApp({ me: identity(), config: enhancedConfig, routes: { "POST /api/jobs": (_, state) => { if (!accepted) { accepted = true; state.jobs = [job("once", "queued", { filename: "once.pdf", engine: "docling" })]; throw new TypeError("Connection lost"); } return response({ jobs: state.jobs, errors: [] }, 202); } } }); await flush();
    app.ui("engine-docling").emit("change"); const source = file("once.pdf"); app.add(source); app.ui("convert-button").click(); await flush();
    assert(app.ui("choose-button").disabled); app.add(file("other.md")); assert.equal(app.ui("queue-count").textContent, "1"); app.ui("engine-markitdown").emit("change"); assert(app.ui("engine-docling").checked);
    app.ui("upload-recheck").click(); app.ui("upload-recheck").click(); await flush();
    const calls = app.calls("/api/jobs", "POST"); assert.equal(calls.length, 2); assert.equal(calls[0].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]); assert(calls[0].options.headers["Idempotency-Key"].length >= 16);
    assert.equal(calls[0].options.body.parts[1][1], source); assert.equal(calls[1].options.body.parts[1][1], source); assert.equal(app.ui("history-count").textContent, "1"); assert(app.ui("upload-recheck").hidden); app.stop();
  });
  await test("a new intentional upload rotates its key and clearing uncertainty asks first", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => response({ detail: "Lost response" }, 503) } }); await flush();
    app.add(file("first.md")); app.ui("convert-button").click(); await flush(); app.window.confirmResult = false; app.ui("clear-button").click(); assert.equal(app.ui("queue-count").textContent, "1");
    app.window.confirmResult = true; app.ui("clear-button").click(); app.add(file("second.md")); app.ui("convert-button").click(); await flush();
    assert.notEqual(app.calls("/api/jobs", "POST")[0].options.headers["Idempotency-Key"], app.calls("/api/jobs", "POST")[1].options.headers["Idempotency-Key"]); assert(app.window.confirmations[0].includes("再次计次")); app.stop();
  });
  await test("partial errors match duplicate filenames by original file index", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/jobs": (_, state) => { state.jobs = [job("accepted", "queued", { filename: "same.md" })]; return response({ jobs: state.jobs, errors: [{ filename: "same.md", file_index: 1, error: "Synthetic rejected file" }] }, 202); } } }); await flush();
    app.add(file("same.md", 10), file("same.md", 11)); app.ui("convert-button").click(); await flush();
    assert.equal(app.ui("queue-count").textContent, "1"); assert(app.ui("file-list").textContent.includes("11 B")); assert(app.ui("notice").textContent.includes("不计次")); app.stop();
  });
  await test("uncertain retry reuses its key until the confirmed attempt changes", async () => {
    const app = createApp({ me: identity(), jobs: [job("retry", "failed")], routes: { "POST /api/jobs/retry/retry": () => response({ detail: "Lost retry response" }, 503) } }); await flush();
    app.history("retry"); await flush(); app.history("retry"); await flush();
    const calls = app.calls("/api/jobs/retry/retry"); assert.equal(calls[0].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]);
    app.state.jobs = [job("retry", "failed", { attempts: 2 })]; app.ui("refresh-button").click(); await flush(); app.history("retry"); await flush();
    assert.notEqual(calls[0].options.headers["Idempotency-Key"], app.calls("/api/jobs/retry/retry")[2].options.headers["Idempotency-Key"]); app.stop();
  });
  await test("stopping task remains polled and cannot retry before physical release", async () => {
    const item = job("stop", "failed", { error: "任务已取消", lifecycle_status: "stopping" });
    const app = createApp({ me: identity(), jobs: [item] }); await flush(); app.history("select");
    assert.equal(app.ui("output-label").textContent, "正在停止"); assert(app.ui("document-retry").disabled); assert(app.ui("job-facts-list").textContent.includes("物理进程退出"));
    assert([...app.timers.values()].some((timer) => timer.delay === 2000)); app.state.jobs = [{ ...item, lifecycle_status: "cancelled" }]; await app.advance(2000); assert(!app.ui("document-retry").disabled); app.stop();
  });
  await test("source and attempt provenance are rendered as text with manifest export and no quality guarantee", async () => {
    const item = job("provenance", "succeeded", { source_sha256: "abcdef", provenance_status: "recorded", quota_charged: true, accepted_attempts: 2, submission_snapshot: { config_version: 1, engine: "markitdown", profile: "<img onerror=1>", engine_version: "1.0" }, attempt_history: [{ id: "attempt-one", sequence: 1, reason: "submission", state: "failed", quota_charged: true, accepted_at: NOW / 1000 }, { id: "attempt-two", sequence: 2, reason: "retry", state: "succeeded", quota_charged: true, accepted_at: NOW / 1000 + 10, snapshot: { config_version: 2, engine: "markitdown", profile: "safe", engine_version: "1.1" } }] });
    const app = createApp({ me: identity(), jobs: [item], routes: { "GET /api/jobs/provenance": () => response(detail(item)) } }); await flush(); app.history("select"); await flush();
    assert(app.ui("job-facts-list").textContent.includes("abcdef")); assert(app.ui("job-facts-list").textContent.includes("<img onerror=1>")); assert.equal(app.ui("job-facts-list").innerHTML, "");
    assert.equal(app.ui("attempt-list").children.length, 2); assert(app.ui("attempt-list").textContent.includes("配置版本 2")); assert(!app.ui("result-review").hidden); app.ui("manifest-download").click(); assert.equal(app.document.downloads[0].href, "/api/jobs/provenance/manifest");
    app.ui("logout-button").click(); await flush(); assert.equal(app.ui("job-facts-list").textContent, ""); assert.equal(app.ui("attempt-list").textContent, ""); assert(app.ui("manifest-download").disabled); app.stop();
  });
  await test("clipboard failure tries legacy copy then exposes and selects the manual source", async () => {
    const item = job("copy"); const app = createApp({ me: identity(), jobs: [item], clipboardFailure: true, routes: { "GET /api/jobs/copy": () => response(detail(item, "Manual source")) } }); await flush(); app.history("select"); await flush(); app.ui("copy-button").click(); await flush();
    assert(!app.ui("source-panel").hidden); assert(app.ui("markdown-source").selected); assert.equal(app.document.body.children.length, 0); assert(app.ui("notice").textContent.includes("系统复制快捷键")); assert(!app.ui("copy-button").textContent.includes("已复制")); app.stop();
    const legacy = createApp({ me: identity(), jobs: [item], clipboardFailure: true, legacyCopy: true, routes: { "GET /api/jobs/copy": () => response(detail(item)) } }); await flush(); legacy.history("select"); await flush(); legacy.ui("copy-button").click(); await flush(); assert(legacy.ui("copy-button").textContent.includes("已复制")); legacy.stop();
  });
  await test("administrator cannot deactivate self even after DOM tampering and invite TTL is bounded", async () => {
    const me = identity({ is_admin: true }); const app = createApp({ me, routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [me.user] }), "POST /api/admin/invites": () => response({ token: "synthetic-only", expires_at: NOW / 1000 + 3600 }) } }); await flush(); app.ui("admin-toggle").click(); await flush();
    const form = app.ui("users-list").querySelector("form"), active = form.querySelector('[data-field="is_active"]'); assert(active.disabled); active.disabled = false; active.checked = false; app.ui("users-list").emit("submit", { target: form }); await flush(); assert.equal(app.calls("/api/admin/users/u1").length, 0); assert(app.ui("admin-status").textContent.includes("不能停用"));
    app.ui("invite-hours").value = "169"; app.ui("invite-create").click(); await flush(); assert.equal(app.calls("/api/admin/invites", "POST").length, 0);
    app.ui("invite-hours").value = "168"; app.ui("invite-create").click(); await flush(); assert.equal(JSON.parse(app.calls("/api/admin/invites", "POST")[0].options.body).ttl_hours, 168); app.stop();
  });
  await test("bounded defaults show current deployment default scope and atomic expected version", async () => {
    const patch = deferred(); const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "PATCH /api/admin/settings": () => patch.promise } }); await flush(); app.ui("admin-toggle").click(); await flush();
    assert(app.ui("setting-quota-help").textContent.includes("部署默认 50")); assert(app.ui("setting-file-help").textContent.includes("新账户")); assert(app.ui("setting-retention-help").textContent.includes("已有到期时间不变"));
    app.ui("setting-daily-quota").value = "1001"; app.ui("settings-form").emit("submit"); await flush(); assert.equal(app.calls("/api/admin/settings", "PATCH").length, 0);
    app.ui("setting-daily-quota").value = "100"; app.ui("setting-retention-hours").value = "1"; app.ui("settings-form").emit("submit"); app.ui("settings-form").emit("submit");
    assert.equal(app.calls("/api/admin/settings", "PATCH").length, 1); assert(app.ui("settings-save").disabled); assert.deepEqual(JSON.parse(app.calls("/api/admin/settings", "PATCH")[0].options.body), { expected_version: 1, changes: { default_daily_quota: 100, retention_seconds: 3600 } });
    assert(app.window.confirmations[0].includes("已有账户和任务到期时间不变")); const updated = { ...settings, version: 2, current: { ...settings.current, default_daily_quota: 100, retention_seconds: 3600 }, effective: { ...settings.effective, default_daily_quota: 100, retention_seconds: 3600 } }; patch.resolve(response(updated)); await flush();
    assert(app.ui("settings-status").textContent.includes("已保存配置版本 2")); assert(app.ui("account-retention").textContent.includes("入队起 1 小时")); assert(!app.ui("settings-save").disabled); app.stop();
  });
  await test("settings conflict refreshes values atomically and never overwrites newer config silently", async () => {
    let changed = false; const fresh = { ...settings, version: 4, current: { ...settings.current, default_daily_quota: 75 } };
    const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "GET /api/admin/settings": () => response(changed ? fresh : settings), "PATCH /api/admin/settings": () => { changed = true; return response({ detail: "Configuration changed" }, 409); } } }); await flush(); app.ui("admin-toggle").click(); await flush(); app.ui("setting-daily-quota").value = "60"; app.ui("settings-form").emit("submit"); await flush();
    assert.equal(app.ui("setting-daily-quota").value, "75"); assert(app.ui("settings-version").textContent.includes("版本 4")); assert(app.ui("settings-status").textContent.includes("本次未保存")); assert.equal(app.calls("/api/admin/settings", "PATCH").length, 1); assert(!app.ui("settings-save").disabled); app.stop();
  });
  await test("unsaved settings edits survive unrelated administrator refresh without advancing base version", async () => {
    let changed = false; const fresh = { ...settings, version: 3, current: { ...settings.current, default_daily_quota: 70 } };
    const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "GET /api/admin/settings": () => response(changed ? fresh : settings), "PATCH /api/admin/settings": () => response({ detail: "Conflict" }, 409) } }); await flush(); app.ui("admin-toggle").click(); await flush();
    app.ui("setting-daily-quota").value = "60"; app.ui("setting-daily-quota").emit("input"); changed = true; app.ui("admin-refresh").click(); await flush(); assert.equal(app.ui("setting-daily-quota").value, "60"); app.ui("settings-form").emit("submit"); await flush();
    assert.equal(JSON.parse(app.calls("/api/admin/settings", "PATCH")[0].options.body).expected_version, 1); app.stop();
  });
  await test("audit records are content-safe text and late settings cannot repopulate logout", async () => {
    const patch = deferred(); const event = { action: "user.update", actor_id: "actor", target_type: "user", target_id: "target", created_at: NOW / 1000, before: { daily_quota: 10 }, after: { daily_quota: "<img onerror=1>" }, result: "success", request_id: "request-one" };
    const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "GET /api/admin/audit": () => response({ events: [event] }), "PATCH /api/admin/settings": () => patch.promise } }); await flush(); app.ui("admin-toggle").click(); await flush();
    assert(app.ui("audit-list").textContent.includes("<img onerror=1>")); assert.equal(app.ui("audit-list").innerHTML, ""); assert(app.ui("service-config").textContent.includes("只" ) || app.ui("service-config").textContent.includes("不可"));
    app.ui("setting-daily-quota").value = "60"; app.ui("settings-form").emit("submit"); app.ui("logout-button").click(); await flush(); patch.resolve(response({ ...settings, version: 2 })); await flush(); assert.equal(app.ui("audit-list").children.length, 0); assert.equal(app.ui("service-config").children.length, 0); assert.equal(app.ui("setting-daily-quota").value, ""); assert(app.ui("settings-save").disabled); app.stop();
  });

  await test("usage refresh adopts changed retention and actual attempt limit without changing existing expiry", async () => {
    const initial = identity(); initial.usage.retention_seconds = 7200; initial.usage.config_version = 3; initial.usage.max_attempts = 2;
    const item = job("limit", "failed", { attempts: 2, attempt_history_complete: false, accepted_attempts: 0 });
    const app = createApp({ me: initial, jobs: [item] }); await flush(); app.history("select");
    assert(app.ui("account-retention").textContent.includes("入队起 2 小时")); assert(app.ui("document-retry").disabled); assert(app.ui("job-facts-list").textContent.includes("旧任务历史不完整"));
    const oldExpiry = app.ui("document-expiry").textContent; app.state.usage = { ...initial.usage, retention_seconds: 3600 }; app.ui("refresh-button").click(); await flush();
    assert(app.ui("account-retention").textContent.includes("入队起 1 小时")); assert.equal(app.ui("document-expiry").textContent, oldExpiry); app.stop();
  });
  await test("all static IDs are unique and label and ARIA references exist", async () => {
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]); assert.equal(new Set(ids).size, ids.length);
    for (const match of html.matchAll(/\b(?:for|aria-controls|aria-describedby|aria-labelledby)="([^"]+)"/g)) for (const ref of match[1].split(" ")) assert(ids.includes(ref), `Missing referenced element ${ref}`);
    assert(html.includes("不等于安全擦除")); assert(html.includes("不是原生 DoclingDocument")); assert(html.includes("没有网页改密"));
  });

  await test("expired history exports metadata manifest while content actions remain unavailable", async () => {
    const app = createApp({ me: identity(), jobs: [job("expired-manifest", "expired", { expires_at: NOW / 1000 - 1 })] }); await flush(); app.history("select");
    assert(app.ui("download-button").disabled); assert(app.ui("copy-button").disabled); assert(!app.ui("manifest-download").disabled); assert(app.ui("job-facts-list").textContent.includes("仅现存处理清单"));
    app.ui("manifest-download").click(); assert.equal(app.document.downloads[0].href, "/api/jobs/expired-manifest/manifest"); app.stop();
  });

  await test("fast completed invite double-click cannot replace its only visible token", async () => {
    let count = 0;
    const app = createApp({ me: identity({ is_admin: true }), routes: { "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }), "POST /api/admin/invites": () => response({ token: `synthetic-${++count}`, expires_at: NOW / 1000 + 86400 }) } });
    await flush(); app.ui("admin-toggle").click(); await flush();
    app.ui("invite-create").click(); await flush(); assert.equal(count, 1); assert(app.ui("invite-create").disabled);
    app.ui("invite-create").click(); app.ui("invite-create").emit("click"); await flush(); assert.equal(count, 1); assert.equal(app.ui("new-invite-token").value, "synthetic-1");
    app.ui("invite-dismiss").click(); assert(!app.ui("invite-create").disabled); app.ui("invite-create").click(); await flush(); assert.equal(count, 2); assert.equal(app.ui("new-invite-token").value, "synthetic-2"); app.stop();
  });

  await test("local development boundary is visible before login and production is conditional", async () => {
    const local = createApp({ config: { ...baseConfig, deployment_mode: "local" } }); await flush(); assert(local.ui("deployment-notice").textContent.includes("没有完整生产文件系统隔离")); local.stop();
    const production = createApp({ config: { ...baseConfig, deployment_mode: "production" } }); await flush(); assert(production.ui("deployment-notice").textContent.includes("验收")); assert(production.ui("deployment-notice").textContent.includes("拒绝解析")); production.stop();
  });

  await test("workspace navigation preserves result and history while admin stays a separate view", async () => {
    const app = createApp({ me: identity({ is_admin: true }), jobs: [job("nav")], routes: { "GET /api/jobs/nav": () => response(detail(job("nav"), "# Navigation result")), "GET /api/admin/invites": () => response({ invites: [] }), "GET /api/admin/users": () => response({ users: [] }) } }); await flush(); app.history("select"); await flush();
    app.ui("nav-history").click(); assert(app.ui("upload-section").hidden); assert(app.ui("submit-section").hidden); assert.equal(app.ui("nav-history").getAttribute("aria-pressed"), "true"); assert.equal(app.ui("markdown-source").value, "# Navigation result");
    app.ui("admin-toggle").click(); await flush(); assert(!app.ui("admin-panel").hidden); assert(app.ui("workspace").hidden);
    app.ui("new-invite-token").value = "synthetic-token"; app.ui("invite-result").hidden = false;
    app.ui("nav-workspace").click(); assert(app.ui("admin-panel").hidden); assert(!app.ui("workspace").hidden); assert(!app.ui("upload-section").hidden); assert.equal(app.ui("new-invite-token").value, ""); assert.equal(app.ui("markdown-source").value, "# Navigation result");
    app.ui("logout-button").click(); await flush(); assert(app.ui("nav-workspace").disabled); assert(app.ui("nav-history").disabled); assert(app.ui("admin-panel").hidden); assert.equal(app.ui("markdown-source").value, ""); app.stop();
  });

  assert(!script.includes("localStorage") && !script.includes("sessionStorage"));
  assert.equal((script.match(/\.innerHTML\s*=/g) || []).length, 1, "Only sanitized server preview may become HTML");
  assert(!html.match(/(?:src|href)="https?:/));
  console.log(`PASS: ${scenarios.length} frontend DOM/state scenarios\n${scenarios.map((name) => `  • ${name}`).join("\n")}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
