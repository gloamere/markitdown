/* Real local installer metadata only. No placeholder URL becomes a download. */
(() => {
  "use strict";
  const status = document.getElementById("download-status");
  const targets = { "macos-arm64": "download-mac", "windows-x64": "download-windows" };
  function node(tag, text, className) { const el = document.createElement(tag); if (text) el.textContent = text; if (className) el.className = className; return el; }
  fetch("/api/downloads", { cache: "no-store", credentials: "omit" }).then(async (response) => {
    if (!response.ok) throw new Error("unavailable");
    const data = await response.json();
    let count = 0;
    const seen = new Set();
    for (const build of Array.isArray(data.downloads) ? data.downloads : []) {
      if (!targets[build.platform] || seen.has(build.platform) || build.signed !== false || !Number.isSafeInteger(build.bytes) || build.bytes < 1 || typeof build.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(build.sha256) || typeof build.version !== "string" || !/^\/static\/downloads\/MarkItDown-[A-Za-z0-9_.-]+-unsigned\.(dmg|exe|zip)$/.test(build.url)) continue;
      seen.add(build.platform); count += 1;
      const entry = document.getElementById(targets[build.platform]).querySelector(".download-entry");
      const link = node("a", build.platform === "macos-arm64" ? "下载 Mac 测试包 ↓" : "下载 Windows 测试包 ↓", "button primary");
      link.href = build.url; link.download = build.filename;
      const meta = node("p", `${build.version} · ${(build.bytes / 1048576).toFixed(1)} MiB · 未签名测试包`, "build-meta");
      const details = node("details", "", "hash-details"); details.append(node("summary", "校验 SHA256"), node("code", build.sha256));
      entry.replaceChildren(link, meta, details);
    }
    status.textContent = count ? "以下入口对应此服务上实际存在的测试构建。当前没有签名正式版；可用平台以各自下载卡片为准。" : "当前没有发布可下载的安装包。工程已提供双平台构建流程；产物生成并加入此服务后才显示下载入口。";
  }).catch(() => { status.textContent = "暂时无法确认安装包状态，请稍后刷新。没有确认的产物不会显示下载链接。"; });
})();
