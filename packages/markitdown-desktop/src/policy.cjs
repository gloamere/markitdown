"use strict";

function serviceOrigin(value) {
  if (typeof value !== "string" || value.length > 2048) throw new Error("请输入服务地址。");
  let url;
  try { url = new URL(value.trim()); } catch { throw new Error("地址格式不正确，例如 https://documents.example.com。"); }
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) throw new Error("远程服务必须使用 HTTPS；HTTP 仅允许本机回环地址。");
  if (url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) throw new Error("请填写服务根地址，不含账号、路径、查询参数或片段。");
  return url.origin;
}

function sameService(value, origin) {
  try { const url = new URL(value); return url.origin === origin && ["http:", "https:"].includes(url.protocol) && !url.username && !url.password; } catch { return false; }
}

function allowedDownload(value, origin) {
  if (typeof value !== "string") return false;
  // Workspace downloads are created from authenticated, checked response blobs.
  if (value.startsWith(`blob:${origin}/`)) return true;
  if (!sameService(value, origin)) return false;
  return /^\/api\/jobs\/[^/]+\/(download|manifest)$/.test(new URL(value).pathname);
}

function safeFilename(value) {
  const name = String(value || "document.md").split(/[\\/]/).pop().replace(/[<>:"|?*\x00-\x1f]/g, "_");
  return name && name !== "." && name !== ".." ? name.slice(0, 180) : "document.md";
}

module.exports = { serviceOrigin, sameService, allowedDownload, safeFilename };
