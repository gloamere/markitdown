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
    const request = app.calls("/api/jobs", "POST")[0]; assert.equal(request.options.body.parts.length, 2); assert(request.options.body.parts.every((part) => part[0] === "files")); assert.equal(request.options.headers["X-CSRF-Token"], identity().csrf_token);
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
  assert(!script.includes("localStorage") && !script.includes("sessionStorage"));
  assert.equal((script.match(/\.innerHTML\s*=/g) || []).length, 1, "Only sanitized server preview may become HTML");
  assert(!html.match(/(?:src|href)="https?:/));
  console.log(`PASS: ${scenarios.length} frontend DOM/state scenarios\n${scenarios.map((name) => `  • ${name}`).join("\n")}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
