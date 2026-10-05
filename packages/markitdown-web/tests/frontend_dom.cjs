/*
 * Dependency-free frontend state regression checks (Node.js 18+).
 * Run from any directory: node packages/markitdown-web/tests/frontend_dom.cjs
 *
 * This minimal DOM double exercises the real app.js event handlers and state.
 * It does not validate browser layout, CSS, the HTML sanitizer, clipboard
 * permissions, download integration, or an actual accessibility tree. Those
 * require browser QA; server conversion/sanitization have separate API tests.
 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const staticRoot = path.resolve(__dirname, "../src/markitdown_web/static");
const assert = require("assert").strict;
class Classes {
  constructor(el) {
    this.el = el;
  }
  toggle(name, enabled) {
    const names = new Set(this.el.className.split(/\s+/).filter(Boolean));
    if (enabled ?? !names.has(name)) names.add(name);
    else names.delete(name);
    this.el.className = [...names].join(" ");
  }
  add(name) {
    this.toggle(name, true);
  }
  remove(name) {
    this.toggle(name, false);
  }
}
class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.className = "";
    this.classList = new Classes(this);
    this.listeners = {};
    this.textContent = "";
    this.hidden = false;
    this.value = "";
    this.disabled = false;
    this.scrollTop = 0;
    this.parent = null;
  }
  append(...els) {
    for (const el of els) {
      if (el.tagName === "fragment") this.append(...el.children);
      else {
        el.parent = this;
        this.children.push(el);
      }
    }
  }
  replaceChildren(...els) {
    this.children = [];
    this.textContent = "";
    this._html = "";
    this.append(...els);
  }
  set innerHTML(value) {
    this._html = value;
    this.children = [];
  }
  get innerHTML() {
    return this._html || "";
  }
  setAttribute(k, v) {
    this.attributes[k] = v;
  }
  getAttribute(k) {
    return this.attributes[k];
  }
  addEventListener(k, fn) {
    (this.listeners[k] ??= []).push(fn);
  }
  emit(k, options = {}) {
    for (const fn of this.listeners[k] || [])
      fn({
        target: this,
        preventDefault() {},
        stopPropagation() {},
        ...options,
      });
  }
  focus() {
    document.activeElement = this;
  }
  select() {
    this.selectionStart = 0;
    this.selectionEnd = this.value.length;
  }
  click() {
    this.emit("click");
  }
  remove() {
    if (this.parent)
      this.parent.children = this.parent.children.filter((x) => x !== this);
  }
  matches(selector) {
    if (selector === "a") return this.tagName === "a";
    if (selector === "button[data-action]")
      return this.tagName === "button" && !!this.dataset.action;
    return [...selector.matchAll(/\[data-([a-z]+)="([^"]+)"\]/g)].every(
      (m) => this.dataset[m[1]] === m[2],
    );
  }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (child.matches(selector)) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest(selector) {
    return this.matches(selector) ? this : this.parent?.closest(selector);
  }
}
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const elements = Object.fromEntries(
  [...html.matchAll(/id="([^"]+)"/g)].map((m) => [m[1], new Element()]),
);
const document = {
  activeElement: null,
  body: new Element("body"),
  getElementById: (id) => elements[id],
  createElement: (tag) => new Element(tag),
  createDocumentFragment: () => new Element("fragment"),
  listeners: {},
  addEventListener(k, v) {
    this.listeners[k] = v;
  },
};
const window = {
  listeners: {},
  addEventListener(k, v) {
    this.listeners[k] = v;
  },
};
const config = {
  max_file_bytes: 20 * 1024 * 1024,
  max_total_bytes: 50 * 1024 * 1024,
  max_files: 10,
  extensions: [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"],
};
let convertHandler;
let configHandler = async () => ({ ok: true, json: async () => config });
const requests = [];
const fetch = async (url, options) => {
  requests.push({ url, options });
  return url === "/api/config" ? configHandler() : convertHandler(url, options);
};
let copied = "";
const urls = new Set();
const url = {
  createObjectURL() {
    const u = `blob:test-${urls.size}`;
    urls.add(u);
    return u;
  },
  revokeObjectURL(u) {
    urls.delete(u);
  },
};
class FormData {
  constructor() {
    this.parts = [];
  }
  append(...v) {
    this.parts.push(v);
  }
}
vm.runInNewContext(fs.readFileSync(path.join(staticRoot, "app.js"), "utf8"), {
  document,
  window,
  fetch,
  FormData,
  Blob,
  URL: url,
  navigator: {
    clipboard: {
      writeText: async (text) => {
        copied = text;
      },
    },
  },
  AbortController,
  setTimeout,
  console,
});
const ui = (id) => elements[id];
const tick = () => new Promise((r) => setImmediate(r));
const file = (name, size = 12, lastModified = 1) => ({
  name,
  size,
  lastModified,
});
function add(...files) {
  ui("file-input").files = files;
  ui("file-input").emit("change");
}
function clickFile(action, index = 0) {
  const button = ui("file-list").querySelectorAll(`[data-action="${action}"]`)[
    index
  ];
  assert(button, action);
  ui("file-list").emit("click", { target: button });
}
function done(filename, text) {
  return { filename, markdown: text, html: `<p>${text}</p>`, error: null };
}
function payload(results) {
  return { ok: true, json: async () => ({ results }) };
}
(async () => {
  await tick();
  assert(!ui("choose-button").disabled);
  assert.equal(ui("queue-count").textContent, "0");
  add(file("plan.md"), file("bad.pdf"), file("<script>.exe"));
  assert.equal(ui("queue-count").textContent, "3");
  assert.equal(ui("file-list").children[2].dataset.state, "error");
  const deferred = {};
  convertHandler = async (url, options) => {
    assert.equal(options.headers["X-MarkItDown-Request"], "1");
    assert.equal(options.body.parts.length, 2);
    return new Promise((resolve) => {
      deferred.resolve = resolve;
    });
  };
  ui("convert-button").click();
  ui("convert-button").click();
  assert.equal(requests.filter((x) => x.url === "/api/convert").length, 1);
  assert(ui("choose-button").disabled);
  assert(ui("convert-button").disabled);
  deferred.resolve(
    payload([
      done("plan.md", "# Plan"),
      { filename: "bad.pdf", error: "Broken <img onerror=alert(1)>" },
    ]),
  );
  await tick();
  assert.equal(ui("file-list").children[0].dataset.state, "done");
  assert.equal(ui("file-list").children[1].dataset.state, "error");
  assert(ui("markdown-preview").innerHTML.includes("# Plan"));
  assert(ui("conversion-summary").textContent.includes("1 个完成"));
  ui("source-tab").click();
  assert.equal(ui("markdown-source").value, "# Plan");
  assert(!ui("source-panel").hidden);
  ui("preview-tab").click();
  assert.equal(ui("markdown-source").value, "# Plan");
  ui("source-tab").emit("keydown", { key: "End" });
  assert.equal(ui("source-tab").attributes["aria-selected"], "true");
  ui("copy-button").click();
  await tick();
  assert.equal(copied, "# Plan");
  ui("download-button").click();
  assert.equal(document.body.children.length, 0);
  assert.equal(urls.size, 1);
  convertHandler = async (url, options) => {
    assert.equal(options.body.parts.length, 1);
    return payload([done("bad.pdf", "Recovered")]);
  };
  clickFile("retry");
  await tick();
  assert.equal(ui("markdown-preview").innerHTML, "<p>Recovered</p>");
  ui("clear-button").click();
  assert.equal(urls.size, 0);
  assert.equal(ui("queue-count").textContent, "0");
  assert.equal(ui("markdown-source").value, "");
  assert.equal(ui("markdown-preview").innerHTML, "");
  add(file("empty.txt", 0), file("huge.txt", 20 * 1024 * 1024 + 1));
  assert(ui("convert-button").disabled);
  assert.equal(
    ui("file-list").children.filter((x) => x.dataset.state === "error").length,
    2,
  );
  ui("clear-button").click();
  add(...Array.from({ length: 11 }, (_, i) => file(`${i}.txt`)));
  assert.equal(ui("queue-count").textContent, "10");
  assert(ui("notice").textContent.includes("数量已达上限"));
  ui("clear-button").click();
  add(
    file("one.txt", 20 * 1024 * 1024),
    file("two.txt", 20 * 1024 * 1024),
    file("three.txt", 11 * 1024 * 1024),
  );
  assert.equal(ui("file-list").children[2].dataset.state, "error");
  assert.equal(ui("queue-size").textContent, "40 MiB");
  ui("clear-button").click();
  add(file("same.md"));
  add(file("same.md"));
  assert.equal(ui("queue-count").textContent, "1");
  assert(ui("notice").textContent.includes("重复"));
  ui("clear-button").click();
  // A late response after reset must never revive or replace old document state.
  let oldResolve;
  convertHandler = async () =>
    new Promise((resolve) => {
      oldResolve = resolve;
    });
  add(file("old.txt"));
  ui("convert-button").click();
  ui("clear-button").click();
  convertHandler = async () => payload([done("new.txt", "Fresh")]);
  add(file("new.txt"));
  ui("convert-button").click();
  await tick();
  assert.equal(ui("markdown-preview").innerHTML, "<p>Fresh</p>");
  oldResolve(payload([done("old.txt", "STALE")]));
  await tick();
  assert.equal(ui("markdown-preview").innerHTML, "<p>Fresh</p>");
  assert.equal(ui("queue-count").textContent, "1");
  ui("clear-button").click();
  // Remove one member of an in-flight batch while retaining response indexes for the rest.
  let removeResolve;
  convertHandler = async () =>
    new Promise((resolve) => {
      removeResolve = resolve;
    });
  add(file("remove.md"), file("keep.md"));
  ui("convert-button").click();
  clickFile("remove", 0);
  removeResolve(
    payload([done("remove.md", "Removed"), done("keep.md", "Kept")]),
  );
  await tick();
  assert.equal(ui("queue-count").textContent, "1");
  assert.equal(ui("markdown-preview").innerHTML, "<p>Kept</p>");
  ui("clear-button").click();
  // Interrupted navigation/back-forward-cache restores a retryable state.
  let navigationResolve;
  convertHandler = async () =>
    new Promise((resolve) => {
      navigationResolve = resolve;
    });
  add(file("navigation.md"));
  ui("convert-button").click();
  window.listeners.pagehide();
  window.listeners.pageshow({ persisted: true });
  assert(!ui("convert-button").disabled);
  assert.equal(ui("file-list").children[0].dataset.state, "error");
  navigationResolve(payload([done("navigation.md", "STALE")]));
  await tick();
  assert.equal(ui("file-list").children[0].dataset.state, "error");
  ui("clear-button").click();
  // Structured non-2xx and malformed-success errors are recoverable per file.
  convertHandler = async () => ({
    ok: false,
    status: 413,
    json: async () => ({ detail: "Too large" }),
  });
  add(file("fail.md"));
  ui("convert-button").click();
  await tick();
  assert(ui("empty-description").textContent.includes("Too large"));
  assert(!ui("convert-button").disabled);
  convertHandler = async () => payload([]);
  clickFile("retry");
  await tick();
  assert(ui("empty-description").textContent.includes("不完整"));
  ui("clear-button").click();
  // Server config failure disables upload; retry reconnects without a reload.
  configHandler = async () => ({ ok: false });
  ui("reconnect-button").click();
  await tick();
  assert(ui("choose-button").disabled);
  assert(!ui("connection-notice").hidden);
  configHandler = async () => ({ ok: true, json: async () => config });
  ui("reconnect-button").click();
  await tick();
  assert(!ui("choose-button").disabled);
  assert(ui("connection-notice").hidden);
  assert(
    requests.every((x) => x.url === "/api/config" || x.url === "/api/convert"),
  );
  console.log(
    "PASS: 17 DOM/state scenarios: mixed response, hostile text, repeated click, source/preview, keyboard tabs, copy, download cleanup, individual retry, reset, empty/size/count/total/dedupe validation, stale response, in-flight removal, back-forward restoration, API error/malformed response, config reconnect. All fetches remain same-origin.",
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
