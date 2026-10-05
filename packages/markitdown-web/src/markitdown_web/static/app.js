/* Local-only UI: document contents stay in memory and go only to /api/convert. */
(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const ui = {
    input: byId("file-input"), choose: byId("choose-button"), drop: byId("drop-zone"),
    clear: byId("clear-button"), convert: byId("convert-button"), convertLabel: byId("convert-label"),
    list: byId("file-list"), queueEmpty: byId("queue-empty"), count: byId("queue-count"),
    size: byId("queue-size"), summary: byId("conversion-summary"), notice: byId("notice"),
    connection: byId("connection-notice"), connectionMessage: byId("connection-message"),
    reconnect: byId("reconnect-button"), limits: byId("upload-limits"), formats: byId("format-list"),
    previewTab: byId("preview-tab"), sourceTab: byId("source-tab"),
    previewPanel: byId("preview-panel"), sourcePanel: byId("source-panel"),
    preview: byId("markdown-preview"), source: byId("markdown-source"),
    empty: byId("output-empty"), emptyTitle: byId("empty-title"), emptyDescription: byId("empty-description"),
    heading: byId("document-heading"), name: byId("document-name"), length: byId("document-length"),
    outputLabel: byId("output-label"), copy: byId("copy-button"), download: byId("download-button"),
    actionStatus: byId("action-status"),
  };
  const defaults = {
    max_file_bytes: 20 * 1024 * 1024,
    max_total_bytes: 50 * 1024 * 1024,
    max_files: 10,
    extensions: [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"],
  };
  let config = { ...defaults };
  let configReady = false;
  let configLoading = false;
  let entries = [];
  let nextId = 1;
  let selectedId = null;
  let view = "preview";
  let busy = false;
  let controller = null;
  let generation = 0;
  let dragDepth = 0;
  let renderedDocument = null;
  const objectUrls = new Set();

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace(/\.0$/, "")} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MiB`;
  }

  function extension(name) {
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot).toLowerCase();
  }

  function announce(message) { ui.actionStatus.textContent = message; }
  function showNotice(message) {
    ui.notice.textContent = message;
    ui.notice.hidden = !message;
  }
  function selectedEntry() { return entries.find((entry) => entry.id === selectedId); }
  function eligible(entry) { return !entry.validationError && (entry.state === "queued" || entry.state === "error"); }
  function queuedBytes() { return entries.reduce((sum, entry) => sum + (entry.validationError ? 0 : entry.file.size), 0); }
  function isDuplicate(file) {
    return entries.some((entry) => entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified);
  }

  function renderQueue() {
    const fragment = document.createDocumentFragment();
    const statusNames = { queued: "等待转换", processing: "转换中…", done: "转换完成", error: "未能转换" };
    for (const entry of entries) {
      const row = document.createElement("li");
      row.className = "file-item";
      row.classList.toggle("is-selected", entry.id === selectedId);
      row.dataset.state = entry.state;
      const select = document.createElement("button");
      select.type = "button";
      select.className = "file-select";
      select.dataset.action = "select";
      select.dataset.id = String(entry.id);
      select.setAttribute("aria-pressed", String(entry.id === selectedId));
      select.setAttribute("aria-label", `${entry.file.name}，${statusNames[entry.state]}，查看文件`);
      const icon = document.createElement("span");
      icon.className = "file-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = extension(entry.file.name).slice(1).toUpperCase().slice(0, 4) || "FILE";
      const details = document.createElement("span");
      details.className = "file-details";
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = entry.file.name;
      name.title = entry.file.name;
      const meta = document.createElement("span");
      meta.className = "file-meta";
      const size = document.createElement("span");
      size.textContent = formatBytes(entry.file.size);
      const status = document.createElement("span");
      status.className = "file-state";
      status.textContent = statusNames[entry.state];
      meta.append(size, status);
      details.append(name, meta);
      if (entry.error) {
        const error = document.createElement("span");
        error.className = "file-error";
        error.textContent = entry.error;
        details.append(error);
      }
      select.append(icon, details);
      row.append(select);
      if (entry.state === "error" && !entry.validationError) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "file-action retry";
        retry.dataset.action = "retry";
        retry.dataset.id = String(entry.id);
        retry.textContent = "重试";
        retry.setAttribute("aria-label", `重新转换 ${entry.file.name}`);
        retry.disabled = busy || !configReady;
        row.append(retry);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "file-action";
      remove.dataset.action = "remove";
      remove.dataset.id = String(entry.id);
      remove.textContent = "×";
      remove.setAttribute("aria-label", `移除 ${entry.file.name}`);
      row.append(remove);
      fragment.append(row);
    }
    ui.list.replaceChildren(fragment);
    ui.count.textContent = String(entries.length);
    ui.size.textContent = formatBytes(queuedBytes());
    ui.queueEmpty.hidden = entries.length > 0;
  }

  function renderDocument() {
    const entry = selectedEntry();
    const done = entry?.state === "done";
    const hasContent = done && entry.markdown.length > 0;
    ui.copy.disabled = !done;
    ui.download.disabled = !done;
    ui.heading.hidden = !entry;
    ui.name.textContent = entry ? entry.filename || entry.file.name : "";
    ui.name.title = ui.name.textContent;
    ui.length.textContent = done ? `${entry.markdown.length.toLocaleString("zh-CN")} 字符` : "";
    ui.outputLabel.textContent = done ? "已完成" : entry?.state === "processing" ? "正在转换" : entry?.state === "error" ? "需要处理" : "等待转换";
    ui.empty.hidden = hasContent;
    ui.previewPanel.hidden = !hasContent || view !== "preview";
    ui.sourcePanel.hidden = !hasContent || view !== "source";
    ui.previewTab.setAttribute("aria-selected", String(view === "preview"));
    ui.sourceTab.setAttribute("aria-selected", String(view === "source"));
    ui.previewTab.tabIndex = view === "preview" ? 0 : -1;
    ui.sourceTab.tabIndex = view === "source" ? 0 : -1;
    if (done && renderedDocument !== entry) {
      // The API returns sanitized HTML with images removed. Never insert filenames or errors as HTML.
      ui.preview.innerHTML = entry.html;
      for (const link of ui.preview.querySelectorAll("a")) {
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
      }
      ui.source.value = entry.markdown;
      ui.source.scrollTop = 0;
      ui.previewPanel.scrollTop = 0;
      renderedDocument = entry;
    } else if (!done) {
      ui.preview.replaceChildren();
      ui.source.value = "";
      renderedDocument = null;
    }
    if (!hasContent) {
      if (entry?.state === "processing") {
        ui.emptyTitle.textContent = "正在整理文字…";
        ui.emptyDescription.textContent = "文件正在本机转换\n较大的文档可能需要一些时间";
      } else if (entry?.state === "error") {
        ui.emptyTitle.textContent = "这个文件暂时无法转换";
        ui.emptyDescription.textContent = entry.error;
      } else if (done) {
        ui.emptyTitle.textContent = "没有可预览的文字";
        ui.emptyDescription.textContent = "转换已完成，但结果为空\n请确认文档包含可提取的文字";
      } else if (entry) {
        ui.emptyTitle.textContent = "文件已就位";
        ui.emptyDescription.textContent = "点击「开始转换」\n让文档回到清晰的 Markdown";
      } else {
        ui.emptyTitle.textContent = "下一站，Markdown";
        ui.emptyDescription.textContent = "添加文件并开始转换\n在这里预览结果，或查看 Markdown 源码";
      }
    }
  }

  function render() {
    const active = document.activeElement;
    const focusedAction = active?.dataset.action;
    const focusedId = active?.dataset.id;
    renderQueue();
    renderDocument();
    // Preserve keyboard focus when rebuilding the queue after state changes.
    if (focusedAction && focusedId) {
      const replacement = ui.list.querySelector(`[data-action="${focusedAction}"][data-id="${focusedId}"]`);
      if (replacement && !replacement.disabled) replacement.focus({ preventScroll: true });
    }
    ui.clear.disabled = entries.length === 0;
    ui.choose.disabled = busy || !configReady;
    ui.input.disabled = busy || !configReady;
    ui.drop.classList.toggle("is-busy", busy);
    ui.convert.disabled = busy || !configReady || !entries.some(eligible);
    ui.convert.classList.toggle("is-busy", busy);
    ui.convertLabel.textContent = busy ? "正在转换…" : "开始转换";
    ui.list.setAttribute("aria-busy", String(busy));
    if (!configReady) {
      ui.summary.textContent = configLoading ? "正在连接本机服务…" : "本机服务未连接，请重新连接后继续";
    } else if (busy) {
      ui.summary.textContent = "正在本机处理文件，清空可停止等待并移除结果";
    } else if (!entries.length) {
      ui.summary.textContent = "文件不会上传到第三方服务";
    } else {
      const done = entries.filter((entry) => entry.state === "done").length;
      const errors = entries.filter((entry) => entry.state === "error").length;
      const waiting = entries.filter((entry) => entry.state === "queued").length;
      ui.summary.textContent = [done ? `${done} 个完成` : "", waiting ? `${waiting} 个待转换` : "", errors ? `${errors} 个未能转换` : ""].filter(Boolean).join(" · ");
    }
  }

  async function loadConfig() {
    if (configLoading) return;
    configLoading = true;
    ui.reconnect.disabled = true;
    render();
    try {
      const response = await fetch("/api/config", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error("连接本机服务失败，请确认服务正在运行。");
      const data = await response.json();
      for (const key of ["max_file_bytes", "max_total_bytes", "max_files"]) {
        if (!Number.isSafeInteger(data[key]) || data[key] < 1) throw new Error("服务配置无效，请检查本机服务后重试。");
      }
      if (!Array.isArray(data.extensions) || !data.extensions.length || data.extensions.some((value) => typeof value !== "string")) {
        throw new Error("服务配置无效，请检查本机服务后重试。");
      }
      const extensions = [...new Set(data.extensions.map((value) => `${value.startsWith(".") ? "" : "."}${value.toLowerCase()}`))].filter((value) => defaults.extensions.includes(value));
      if (!extensions.length) throw new Error("本机服务没有可用的文件格式。");
      config = {
        max_file_bytes: Math.min(data.max_file_bytes, defaults.max_file_bytes),
        max_total_bytes: Math.min(data.max_total_bytes, defaults.max_total_bytes),
        max_files: Math.min(data.max_files, defaults.max_files),
        extensions,
      };
      configReady = true;
      ui.connection.hidden = true;
      ui.input.accept = config.extensions.join(",");
      ui.limits.textContent = `最多 ${config.max_files} 个文件 · 单个 ${formatBytes(config.max_file_bytes)} · 总计 ${formatBytes(config.max_total_bytes)}`;
      ui.formats.replaceChildren(...config.extensions.map((value) => {
        const tag = document.createElement("span");
        tag.textContent = value.slice(1).toUpperCase();
        return tag;
      }));
    } catch (error) {
      configReady = false;
      ui.connectionMessage.textContent = error instanceof TypeError ? "无法连接本机服务，请确认服务正在运行后重试。" : error.message || "无法读取本机服务配置，请重试。";
      ui.connection.hidden = false;
    } finally {
      configLoading = false;
      ui.reconnect.disabled = false;
      render();
    }
  }

  function addFiles(files) {
    if (busy) { showNotice("正在转换文件，请等待完成，或清空后重新选择。"); return; }
    if (!configReady) { showNotice("请先连接本机服务，再添加文件。"); return; }
    const messages = [];
    let added = 0;
    for (const file of files) {
      if (isDuplicate(file)) { messages.push(`已跳过重复文件：${file.name}`); continue; }
      if (entries.length >= config.max_files) { messages.push(`文件数量已达上限，未添加：${file.name}`); continue; }
      let error = null;
      if (!config.extensions.includes(extension(file.name))) error = "暂不支持此文件格式，请选择下方列出的格式。";
      else if (file.size === 0) error = "这是一个空文件，请选择包含内容的文件。";
      else if (file.size > config.max_file_bytes) error = `文件超过 ${formatBytes(config.max_file_bytes)} 的单个文件限制。`;
      else if (queuedBytes() + file.size > config.max_total_bytes) error = `文件总大小将超过 ${formatBytes(config.max_total_bytes)}，请移除部分文件后重新添加。`;
      const entry = { id: nextId++, file, state: error ? "error" : "queued", error, validationError: Boolean(error), markdown: "", html: "", filename: "" };
      entries.push(entry);
      if (selectedId === null) selectedId = entry.id;
      added += 1;
    }
    ui.input.value = "";
    showNotice(messages.join("\n"));
    render();
    if (added) announce(`已添加 ${added} 个文件。${ui.summary.textContent}`);
  }

  async function convertFiles(onlyId = null) {
    if (busy || !configReady) return;
    const batch = entries.filter((entry) => eligible(entry) && (onlyId === null || entry.id === onlyId));
    if (!batch.length) return;
    showNotice("");
    const requestGeneration = ++generation;
    controller = new AbortController();
    const requestController = controller;
    busy = true;
    for (const entry of batch) { entry.state = "processing"; entry.error = null; }
    if (onlyId !== null) selectedId = onlyId;
    render();
    const form = new FormData();
    for (const entry of batch) form.append("files", entry.file, entry.file.name);
    try {
      const response = await fetch("/api/convert", {
        method: "POST", body: form, signal: requestController.signal,
        headers: { "X-MarkItDown-Request": "1" }, credentials: "same-origin", cache: "no-store",
      });
      let payload;
      try { payload = await response.json(); } catch { throw new Error("本机服务未返回有效结果，请重试。"); }
      if (requestGeneration !== generation) return;
      if (!response.ok) throw new Error(typeof payload?.detail === "string" ? payload.detail : `转换请求失败（${response.status}），请重试。`);
      if (!Array.isArray(payload?.results) || payload.results.length !== batch.length) throw new Error("返回的文件结果不完整，请重试。");
      batch.forEach((entry, index) => {
        if (!entries.includes(entry)) return;
        const result = payload.results[index];
        if (typeof result?.error === "string" && result.error) {
          entry.state = "error";
          entry.error = result.error;
        } else if (result?.error == null && typeof result?.markdown === "string" && typeof result?.html === "string" && typeof result?.filename === "string") {
          entry.state = "done";
          entry.markdown = result.markdown;
          entry.html = result.html;
          entry.filename = result.filename;
        } else {
          entry.state = "error";
          entry.error = "这个文件的转换结果无效，请重试。";
        }
      });
    } catch (error) {
      if (requestGeneration !== generation || error.name === "AbortError") return;
      for (const entry of batch) {
        if (!entries.includes(entry)) continue;
        entry.state = "error";
        entry.error = error instanceof TypeError ? "连接中断，请检查本机服务后重试。" : error.message || "转换失败，请重试。";
      }
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        controller = null;
        render();
        announce(`转换结束。${ui.summary.textContent}`);
      }
    }
  }

  function reset() {
    generation += 1;
    controller?.abort();
    controller = null;
    busy = false;
    entries = [];
    selectedId = null;
    view = "preview";
    renderedDocument = null;
    ui.input.value = "";
    dragDepth = 0;
    ui.drop.classList.remove("drag-over");
    showNotice("");
    for (const url of objectUrls) URL.revokeObjectURL(url);
    objectUrls.clear();
    render();
    announce("已清空所有文件和转换结果。");
    ui.choose.focus();
  }

  function removeEntry(id) {
    const index = entries.findIndex((entry) => entry.id === id);
    if (index < 0) return;
    const removed = entries[index];
    entries.splice(index, 1);
    if (selectedId === id) selectedId = entries[Math.min(index, entries.length - 1)]?.id ?? null;
    if (busy && !entries.some((entry) => entry.state === "processing")) {
      generation += 1;
      controller?.abort();
      controller = null;
      busy = false;
    }
    render();
    announce(`已移除 ${removed.file.name}`);
    const next = ui.list.querySelector(`[data-action="select"][data-id="${selectedId}"]`);
    (next || ui.choose).focus({ preventScroll: true });
  }

  function switchView(nextView) {
    view = nextView;
    renderDocument();
  }

  function safeDownloadName(name) {
    const basename = name.split(/[\\/]/).pop().replace(/[<>:"|?*\u0000-\u001f\u007f]/g, "_").replace(/[. ]+$/, "");
    const stem = basename.replace(/\.[^.]+$/, "").slice(0, 180).replace(/[. ]+$/, "") || "document";
    return `${stem}.md`;
  }

  async function copyMarkdown() {
    const entry = selectedEntry();
    if (entry?.state !== "done") return;
    const copyGeneration = generation;
    const id = entry.id;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(entry.markdown);
      if (copyGeneration === generation && selectedId === id && entries.includes(entry)) {
        announce("Markdown 已复制到剪贴板。");
        ui.outputLabel.textContent = "已复制到剪贴板";
      }
    } catch {
      if (copyGeneration !== generation || selectedId !== id || !entries.includes(entry)) return;
      switchView("source");
      ui.source.focus();
      ui.source.select();
      showNotice("浏览器未允许自动复制。已选中源码，请按 Ctrl+C（Mac 使用 ⌘C）复制。");
    }
  }

  function downloadMarkdown() {
    const entry = selectedEntry();
    if (entry?.state !== "done") return;
    const blob = new Blob([entry.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    objectUrls.add(url);
    const link = document.createElement("a");
    link.href = url;
    link.download = safeDownloadName(entry.filename || entry.file.name);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); objectUrls.delete(url); }, 1000);
    announce(`已请求下载 ${link.download}。`);
  }

  ui.choose.addEventListener("click", () => { if (!busy && configReady) ui.input.click(); });
  ui.input.addEventListener("change", () => addFiles(Array.from(ui.input.files)));
  ui.clear.addEventListener("click", reset);
  ui.convert.addEventListener("click", () => convertFiles());
  ui.reconnect.addEventListener("click", loadConfig);
  ui.copy.addEventListener("click", copyMarkdown);
  ui.download.addEventListener("click", downloadMarkdown);
  ui.previewTab.addEventListener("click", () => switchView("preview"));
  ui.sourceTab.addEventListener("click", () => switchView("source"));
  for (const tab of [ui.previewTab, ui.sourceTab]) {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "preview" : event.key === "End" ? "source" : view === "preview" ? "source" : "preview";
      switchView(next);
      (next === "preview" ? ui.previewTab : ui.sourceTab).focus();
    });
  }
  ui.list.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const id = Number(button.dataset.id);
    if (button.dataset.action === "select") { selectedId = id; render(); }
    else if (button.dataset.action === "remove") removeEntry(id);
    else if (button.dataset.action === "retry") convertFiles(id);
  });
  ui.drop.addEventListener("dragenter", (event) => {
    event.preventDefault();
    dragDepth += 1;
    if (!busy && configReady) ui.drop.classList.add("drag-over");
  });
  ui.drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = busy || !configReady ? "none" : "copy";
  });
  ui.drop.addEventListener("dragleave", (event) => {
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) ui.drop.classList.remove("drag-over");
  });
  ui.drop.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    dragDepth = 0;
    ui.drop.classList.remove("drag-over");
    if (event.dataTransfer?.files.length) addFiles(Array.from(event.dataTransfer.files));
  });
  // Keep a missed drop from navigating away and discarding in-memory documents.
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => event.preventDefault());
  window.addEventListener("pagehide", () => {
    // A page may return from the back-forward cache after its fetch was interrupted.
    if (busy) {
      generation += 1;
      controller?.abort();
      controller = null;
      busy = false;
      for (const entry of entries) {
        if (entry.state === "processing") {
          entry.state = "error";
          entry.error = "转换已中断，请重试。";
        }
      }
    }
    for (const url of objectUrls) URL.revokeObjectURL(url);
    objectUrls.clear();
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) render(); });

  loadConfig();
})();
