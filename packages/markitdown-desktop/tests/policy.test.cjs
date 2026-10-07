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
