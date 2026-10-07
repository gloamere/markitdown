"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { serviceOrigin, sameService, allowedDownload, safeFilename } = require("../src/policy.cjs");
test("remote TLS and exact root address are required", () => {
  assert.equal(serviceOrigin(" https://docs.example.com/ "), "https://docs.example.com");
  for (const value of ["http://docs.example.com", "file:///etc/passwd", "javascript:alert(1)", "https://user:pass@docs.example.com", "https://docs.example.com/app", "https://docs.example.com?token=a", "https://docs.example.com/#a", {}, ""]) assert.throws(() => serviceOrigin(value));
  for (const value of ["http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"]) assert.equal(serviceOrigin(value), value);
  assert.throws(() => serviceOrigin("http://127.0.0.1.evil.com"));
});
test("navigation and download destinations cannot leave the selected origin", () => {
  const origin = "https://docs.example.com";
  assert.equal(sameService(`${origin}/app`, origin), true);
  for (const value of ["https://docs.example.com.evil.com/app", "https://evil.com", "file:///tmp/a", "https://user:pass@docs.example.com/"]) assert.equal(sameService(value, origin), false);
  assert.equal(allowedDownload(`blob:${origin}/123`, origin), true);
  assert.equal(allowedDownload(`${origin}/api/jobs/123/download`, origin), true);
  assert.equal(allowedDownload(`${origin}/api/jobs/123/manifest`, origin), true);
  for (const value of ["blob:https://evil.com/a", `${origin}/evil.exe`, `${origin}/api/jobs/123/download/evil`, null]) assert.equal(allowedDownload(value, origin), false);
});
test("server names cannot choose an output directory", () => {
  assert.equal(safeFilename("../../document.md"), "document.md");
  assert.equal(safeFilename("C:\\private\\document.md"), "document.md");
  assert.equal(safeFilename(".."), "document.md");
});

test("suggested export names preserve extensions within a UTF-8 byte budget", () => {
  for (const base of ["x".repeat(300), "资".repeat(160), "🧭".repeat(160), "é".repeat(160)]) for (const extension of [".md", ".json", ".zip"]) {
    const name = safeFilename(base + extension);
    assert(Buffer.byteLength(name, "utf8") <= 180);
    assert(name.endsWith(extension));
    assert(!name.includes("\uFFFD"));
  }
  assert.equal(safeFilename("notes.md"), "notes.md");
});
test("suggested export names avoid Windows devices and trailing dot or space aliases", () => {
  for (const device of ["CON", "prn", "AUX", "nul", "COM1", "COM9", "COM¹", "LPT1", "LPT²", "LPT³", "CONIN$", "CONOUT$"]) {
    assert.equal(safeFilename(`${device}.md`), `document-${device}.md`);
    assert.equal(safeFilename(`${device}.notes.md`), `document-${device}.notes.md`);
    assert.equal(safeFilename(`${device} .notes.md`), `document-${device} .notes.md`);
  }
  assert.equal(safeFilename("name.md... "), "name.md");
  assert.equal(safeFilename(" .. "), "document.md");
  assert.equal(safeFilename("a\u202Eb.md"), "a_b.md");
  assert.equal(safeFilename("COM10.md"), "COM10.md");
  assert.equal(safeFilename("CON" + " ".repeat(200) + "notes.md"), "document-CON.md");
});
test("suggested export names can be created on the current platform", () => {
  const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "markitdown-name-"));
  try {
    for (const source of ["资".repeat(160) + ".md", "🧭".repeat(160) + ".json", "CON.md", "LPT².notes.md", "report.md... "]) {
      const target = path.join(directory, safeFilename(source));
      fs.writeFileSync(target, "synthetic export");
      assert.equal(fs.readFileSync(target, "utf8"), "synthetic export");
    }
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
