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
function createApp({ config = baseConfig, me = null, jobs = [], usage, routes = {} } = {}) {
  const document = { activeElement: null, listeners: {}, downloads: [], addEventListener(kind, fn) { this.listeners[kind] = fn; }, createElement(tag) { return new Element(tag, this); }, createDocumentFragment() { return this.createElement("fragment"); } };
  document.body = document.createElement("body");
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
    if (key === "GET /api/config") return response(state.config);
    if (key === "GET /api/me") return state.me ? response(state.me, 200, state.me.user.id) : response({ detail: "Not authenticated" }, 401);
    if (key === "GET /api/jobs") return response({ jobs: state.jobs, usage: state.usage }, 200, state.me?.user.id || "u1");
    if (key === "POST /api/auth/logout") { state.me = null; return response({ status: "ok" }); }
    throw new Error(`Unexpected fetch: ${key}`);
  };
  vm.runInNewContext(script, { document, window, fetch, FormData, Blob, AbortController, Date: FakeDate, URL: { createObjectURL() { const url = `blob:test-${++timerId}`; urls.add(url); return url; }, revokeObjectURL(url) { urls.delete(url); revoked.push(url); } }, navigator: { clipboard: { writeText: async (text) => copies.push(text) } }, setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: time + delay, delay }); return id; }, clearTimeout(id) { timers.delete(id); }, console });
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
  await test("ambiguous upload failure blocks accidental duplicate until the user checks history", async () => {
    const app = createApp({ me: identity(), routes: { "POST /api/jobs": () => response({ detail: "Upload response interrupted" }, 503) } }); await flush();
    app.add(file("uncertain.md")); app.ui("convert-button").click(); await flush();
    assert(app.ui("convert-button").disabled); assert(!app.ui("upload-recheck").hidden); assert(app.ui("notice").textContent.includes("核对转换记录"));
    app.ui("convert-button").emit("click"); assert.equal(app.calls("/api/jobs", "POST").length, 1);
    app.ui("upload-recheck").click(); assert(!app.ui("convert-button").disabled);
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
    assert(css.includes("@media (max-width: 520px)")); assert(css.includes("min-height: 44px"));
    assert(css.includes("@media (prefers-reduced-motion: reduce)")); assert(css.includes("@media (forced-colors: active)"));
    assert(css.includes(".output-content.is-split")); assert(!css.includes("@import"));
  });
  assert(!script.includes("localStorage") && !script.includes("sessionStorage"));
  assert.equal((script.match(/\.innerHTML\s*=/g) || []).length, 1, "Only sanitized server preview may become HTML");
  assert(!html.match(/(?:src|href)="https?:/));
  console.log(`PASS: ${scenarios.length} frontend DOM/state scenarios\n${scenarios.map((name) => `  • ${name}`).join("\n")}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
