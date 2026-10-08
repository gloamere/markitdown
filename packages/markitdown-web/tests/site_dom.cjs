/* Dependency-free rendering checks; real browser layout remains a separate gate. */
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path"), assert = require("node:assert/strict");
const script = fs.readFileSync(path.join(__dirname, "../src/markitdown_web/static/site.js"), "utf8");
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ""; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  querySelector() { return this.entry; }
  text() { return this.textContent + this.children.map((item) => item.text()).join(""); }
}
const build = (extra = {}) => ({ platform: "macos-arm64", version: "1.1.0-dev.1", bytes: 123, sha256: "a".repeat(64), signed: false, filename: "MarkItDown-1.1.0-dev.1-mac-arm64-unsigned.dmg", url: "/static/downloads/MarkItDown-1.1.0-dev.1-mac-arm64-unsigned.dmg", ...extra });
async function run(downloads, failed = false) {
  const elements = Object.fromEntries(["download-status", "download-mac", "download-windows"].map((id) => [id, new Element("div")]));
  elements["download-mac"].entry = new Element("div"); elements["download-windows"].entry = new Element("div");
  vm.runInNewContext(script, { document: { getElementById: (id) => elements[id], createElement: (tag) => new Element(tag) }, fetch: async (url, options) => { assert.equal(url, "/api/downloads"); assert.equal(options.credentials, "omit"); assert.equal(options.cache, "no-store"); return { ok: !failed, json: async () => ({ downloads }) }; } });
  await new Promise((resolve) => setImmediate(resolve));
  return elements;
}
(async () => {
  const valid = await run([build({ source_commit: "c".repeat(40) })]);
  const mac = valid["download-mac"].entry;
  assert.equal(mac.children[0].href, build().url); assert.equal(mac.children[0].download, build().filename);
  assert(mac.text().includes("构建提交 " + "c".repeat(12))); assert.equal(mac.children[2].title, "c".repeat(40));
  const legacy = await run([build()]); assert(legacy["download-mac"].entry.text().includes("未提供提交记录"));
  for (const changed of [{ version: "9.0.0" }, { platform: "windows-x64" }, { filename: "../secret.dmg" }, { url: "https://other.example/a.dmg" }, { sha256: "bad" }, { signed: true }, { bytes: 0 }, { version: "<script>" }]) {
    const invalid = await run([build(changed)]); assert.equal(invalid["download-mac"].entry.children.length, 0); assert.equal(invalid["download-windows"].entry.children.length, 0); assert(invalid["download-status"].text().includes("没有发布"));
  }
  const unknown = await run([build({ source_commit: "<script>" })]); assert(unknown["download-mac"].entry.text().includes("未提供提交记录"));
  const windows = build({ platform: "windows-x64", filename: "MarkItDown-1.1.0-dev.1-win-x64-unsigned.exe", url: "/static/downloads/MarkItDown-1.1.0-dev.1-win-x64-unsigned.exe" });
  const both = await run([build(), windows, build()]); assert.equal(both["download-mac"].entry.children.length, 4); assert.equal(both["download-windows"].entry.children.length, 4);
  const offline = await run([], true); assert(offline["download-status"].text().includes("无法确认"));
  console.log("PASS: download UI version/platform binding, provenance/unknown state, invalid entries, duplicate platforms and network failure");
})().catch((error) => { console.error(error); process.exitCode = 1; });
