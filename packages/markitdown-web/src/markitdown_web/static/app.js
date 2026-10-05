/* Same-origin, invitation-only workspace. Sessions and document previews stay in memory. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "auth-section", "session-section", "bootstrap-notice", "login-tab", "register-tab", "login-form", "register-form",
    "login-username", "login-password", "register-username", "register-password", "invite-token", "login-button", "register-button", "auth-message",
    "logout-button", "logout-retry", "account-name", "account-role", "quota-summary", "quota-reset", "connection-notice", "connection-message", "reconnect-button", "notice",
    "file-input", "choose-button", "drop-zone", "clear-button", "convert-button", "convert-label", "file-list", "queue-empty", "queue-count", "queue-size", "conversion-summary", "upload-limits", "format-list",
    "history-list", "history-count", "history-empty", "history-note", "refresh-button", "archive-button", "archive-count",
    "preview-tab", "source-tab", "preview-panel", "source-panel", "markdown-preview", "markdown-source", "output-empty", "empty-title", "empty-description", "document-heading", "document-name", "document-length", "document-expiry", "output-label", "copy-button", "download-button", "action-status",
    "admin-toggle", "admin-panel", "admin-refresh", "admin-status", "invite-create", "invite-result", "new-invite-token", "invite-expiry", "invite-copy", "invite-dismiss", "invites-list", "users-list",
  ].map((id) => [id, $(id)]));
  const defaults = { max_files: 10, max_file_bytes: 20 * 1024 * 1024, max_total_bytes: 50 * 1024 * 1024, extensions: [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"] };
  const statusNames = { queued: "排队中", running: "转换中…", succeeded: "已完成", failed: "转换失败", expired: "已过期" };
  let config = { ...defaults, has_admin: false }, ready = false, connecting = false, connectionSequence = 0;
  let session = null, usage = null, epoch = 0, authBusy = false, authMode = "login", logoutToken = null, logoutUserId = null, logoutBusy = false;
  let pending = [], nextFileId = 1, jobs = [], selectedId = null, selectedDocument = null, view = "preview", renderedDocument = null;
  let uploadBusy = false, archiveBusy = false, jobsBusy = false, jobsSequence = 0, detailSequence = 0, detailLoading = false, detailError = "", detailController = null;
  let pollTimer = null, expiryTimer = null, dragDepth = 0, adminSequence = 0, adminBusy = false, inviteBusy = false, sessionRefreshBusy = false;
  const controllers = new Set(), objectUrls = new Set(), archiveIds = new Set(), jobMutations = new Set();

  class StaleResponse extends Error {}
  function stale(error) { return error instanceof StaleResponse || error.name === "AbortError"; }
  function message(error) { return error instanceof TypeError ? "无法连接服务，请检查网络后重试。" : error.message || "操作未完成，请重试。"; }
  function notice(text) { ui.notice.textContent = text; ui.notice.hidden = !text; }
  function announce(text) { ui["action-status"].textContent = text; }
  function authMessage(text, error = false) { ui["auth-message"].textContent = text; ui["auth-message"].classList.toggle("is-error", error); }
  function adminMessage(text, error = false) { ui["admin-status"].textContent = text; ui["admin-status"].classList.toggle("is-error", error); }
  function bytes(value) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1).replace(/\.0$/, "")} KiB` : `${(value / 1048576).toFixed(1).replace(/\.0$/, "")} MiB`; }
  function utc(value) { const date = new Date(Number(value) * 1000); return Number.isFinite(date.getTime()) ? `${date.toISOString().slice(0, 16).replace("T", " ")} UTC` : "时间未知"; }
  function extension(name) { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); }
  function status(job) { return job.expires_at && job.expires_at * 1000 <= Date.now() ? "expired" : job.status; }
  function selectedJob() { return jobs.find((job) => String(job.id) === selectedId); }
  function fileLimit() { return Math.min(config.max_file_bytes, usage?.max_file_bytes || session?.user.max_file_bytes || config.max_file_bytes); }
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function actionButton(action, id, text, label, disabled = false) { const node = element("button", "file-action", text); node.type = "button"; node.dataset.action = action; node.dataset.id = String(id); node.setAttribute("aria-label", label); node.disabled = disabled; return node; }
  function revokeUrls() { for (const url of objectUrls) URL.revokeObjectURL(url); objectUrls.clear(); }
  function clearInvite() { ui["new-invite-token"].value = ""; ui["invite-expiry"].textContent = ""; ui["invite-result"].hidden = true; }

  async function request(path, { method = "GET", body, auth = true, signal, blob = false } = {}) {
    const requestEpoch = epoch;
    const controller = new AbortController();
    controllers.add(controller);
    const relayAbort = () => controller.abort();
    if (signal) { if (signal.aborted) controller.abort(); else signal.addEventListener("abort", relayAbort, { once: true }); }
    const headers = {};
    if (method !== "GET") headers["X-MarkItDown-Request"] = "1";
    if (auth && method !== "GET") headers["X-CSRF-Token"] = session?.csrf_token || "";
    if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
    try {
      const response = await fetch(path, { method, body, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (requestEpoch !== epoch || controller.signal.aborted) throw new StaleResponse();
      const responseUser = response.headers.get("X-MarkItDown-User");
      if (auth && session && ((responseUser !== null && responseUser !== String(session.user.id)) || (response.ok && responseUser === null))) {
        sessionChanged("账户状态已变化，请重新连接以确认当前登录账户。");
        throw new StaleResponse();
      }
      if (!response.ok) {
        let data = {}; try { data = await response.json(); } catch { /* Keep a useful status if the response is not JSON. */ }
        if (requestEpoch !== epoch || controller.signal.aborted) throw new StaleResponse();
        const error = new Error(typeof data.detail === "string" ? data.detail : `请求未完成（${response.status}）`); error.status = response.status;
        if (response.status === 401 && auth && session) { resetSession(); authMessage("登录已失效，请重新登录。", true); }
        throw error;
      }
      const result = blob ? await response.blob() : await response.json();
      if (requestEpoch !== epoch || controller.signal.aborted) throw new StaleResponse();
      return result;
    } finally { controllers.delete(controller); if (signal) signal.removeEventListener("abort", relayAbort); }
  }

  function resetSession() {
    epoch += 1; connectionSequence += 1; connecting = false; ui["reconnect-button"].disabled = false; jobsSequence += 1; detailSequence += 1; adminSequence += 1;
    for (const controller of controllers) controller.abort(); controllers.clear();
    detailController?.abort(); detailController = null;
    clearTimeout(pollTimer); clearTimeout(expiryTimer); pollTimer = expiryTimer = null;
    revokeUrls(); session = usage = null; pending = []; jobs = []; archiveIds.clear(); jobMutations.clear();
    selectedId = null; selectedDocument = renderedDocument = null; detailError = ""; detailLoading = false;
    uploadBusy = archiveBusy = jobsBusy = authBusy = adminBusy = inviteBusy = sessionRefreshBusy = false;
    logoutToken = logoutUserId = null; logoutBusy = false; dragDepth = 0; view = "preview";
    ui["file-input"].value = "";
    for (const id of ["login-username", "login-password", "register-username", "register-password", "invite-token"]) ui[id].value = "";
    ui["drop-zone"].classList.remove("drag-over"); ui["admin-panel"].hidden = true;
    ui["admin-toggle"].setAttribute("aria-expanded", "false"); ui["admin-refresh"].disabled = false; ui["invite-create"].disabled = false; ui["users-list"].replaceChildren(); ui["invites-list"].replaceChildren();
    ui["account-name"].textContent = ""; ui["account-role"].textContent = ""; ui["quota-summary"].textContent = ""; ui["quota-reset"].textContent = "";
    ui["history-note"].textContent = "仅展示当前账户最近 100 条记录"; clearInvite(); adminMessage(""); notice(""); announce(""); render();
  }

  function sessionChanged(text) {
    resetSession(); ready = false;
    ui["connection-message"].textContent = text; ui["connection-notice"].hidden = false;
    authMessage(text, true); render();
  }

  function renderAuth() {
    ui["auth-section"].hidden = !!session; ui["session-section"].hidden = !session;
    ui["bootstrap-notice"].hidden = !ready || config.has_admin;
    const disabled = !ready || !config.has_admin || authBusy || logoutToken !== null;
    ui["login-button"].disabled = disabled; ui["register-button"].disabled = disabled;
    ui["login-tab"].disabled = authBusy; ui["register-tab"].disabled = authBusy;
    ui["login-form"].hidden = authMode !== "login"; ui["register-form"].hidden = authMode !== "register";
    for (const mode of ["login", "register"]) { ui[`${mode}-tab`].setAttribute("aria-selected", String(authMode === mode)); ui[`${mode}-tab`].tabIndex = authMode === mode ? 0 : -1; }
    ui["logout-retry"].hidden = logoutToken === null; ui["logout-retry"].disabled = logoutBusy;
    ui["admin-toggle"].hidden = !session?.user.is_admin;
    if (session) {
      ui["account-name"].textContent = session.user.username; ui["account-role"].textContent = session.user.is_admin ? "管理员" : "成员";
      ui["quota-summary"].textContent = usage ? `今日已用 ${usage.used} / ${usage.daily_quota} · 剩余 ${usage.remaining} 个文件 · ${usage.active_jobs || 0} 项处理中` : "正在读取额度…";
      ui["quota-reset"].textContent = usage ? `每日额度按 UTC 重置 · 下次 ${utc(usage.resets_at)}` : "每日额度按 UTC 重置";
    }
  }

  function renderQueue() {
    const fragment = document.createDocumentFragment();
    for (const entry of pending) {
      const row = element("li", "file-item"); row.dataset.state = entry.error ? "error" : "queued";
      const details = element("div", "file-select"); const text = element("div", "file-details");
      text.append(element("span", "file-name", entry.file.name), element("span", "file-meta", bytes(entry.file.size)));
      if (entry.error) text.append(element("span", "file-error", entry.error));
      details.append(element("span", "file-icon", extension(entry.file.name).slice(1).toUpperCase().slice(0, 4) || "FILE"), text);
      row.append(details, actionButton("remove", entry.id, "×", `移除待上传文件 ${entry.file.name}`, uploadBusy)); fragment.append(row);
    }
    ui["file-list"].replaceChildren(fragment); ui["queue-empty"].hidden = pending.length > 0;
    ui["queue-count"].textContent = String(pending.length); ui["queue-size"].textContent = bytes(pending.reduce((sum, entry) => sum + (entry.error ? 0 : entry.file.size), 0));
    const unavailable = !session || !ready || uploadBusy;
    ui["choose-button"].disabled = unavailable; ui["file-input"].disabled = unavailable;
    ui["clear-button"].disabled = uploadBusy || !pending.length;
    ui["convert-button"].disabled = unavailable || !pending.some((entry) => !entry.error) || usage?.remaining === 0;
    ui["convert-button"].classList.toggle("is-busy", uploadBusy); ui["drop-zone"].classList.toggle("is-busy", unavailable);
    ui["convert-label"].textContent = uploadBusy ? "正在上传…" : "上传并转换";
    ui["file-list"].setAttribute("aria-busy", String(uploadBusy));
    ui["conversion-summary"].textContent = uploadBusy ? "上传完成后可在转换记录中查看进度" : usage?.remaining === 0 ? "今日额度已用完，请等待 UTC 重置" : "上传与重试进入队列后，均计入当日额度";
    ui["upload-limits"].textContent = `最多 ${config.max_files} 个文件 · 单个 ${bytes(fileLimit())} · 总计 ${bytes(config.max_total_bytes)}`;
  }

  function renderHistory() {
    const fragment = document.createDocumentFragment();
    for (const job of jobs) {
      const state = status(job), id = String(job.id), changing = jobMutations.has(id);
      if (state !== "succeeded") archiveIds.delete(id);
      const row = element("li", "file-item"); row.dataset.state = state; row.classList.toggle("is-selected", selectedId === id);
      const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.checked = archiveIds.has(id); checkbox.dataset.action = "archive"; checkbox.dataset.id = id;
      checkbox.disabled = state !== "succeeded" || changing || archiveBusy; checkbox.setAttribute("aria-label", `将 ${job.filename} 加入 ZIP 下载`);
      const select = actionButton("select", id, "", `查看 ${job.filename}，${statusNames[state]}`); select.className = "file-select"; select.setAttribute("aria-pressed", String(selectedId === id));
      const details = element("span", "file-details"); const meta = element("span", "file-meta");
      meta.append(element("span", "", bytes(job.size_bytes || 0)), element("span", "file-state", statusNames[state] || "未知状态"));
      details.append(element("span", "file-name", job.filename), meta, element("span", "file-meta", state === "expired" ? "文件已到期，不再提供下载" : `${utc(job.expires_at)} 到期`));
      if (job.error && state === "failed") details.append(element("span", "file-error", job.error));
      select.append(element("span", "file-icon", extension(job.filename).slice(1).toUpperCase().slice(0, 4) || "FILE"), details);
      row.append(checkbox, select);
      const actions = element("div", "history-actions");
      if (state === "failed") actions.append(actionButton("retry", id, job.attempts >= 3 ? "重试次数已用完" : "重试", `重试 ${job.filename}，计入当日额度`, changing || job.attempts >= 3 || usage?.remaining === 0));
      actions.append(actionButton("delete", id, changing ? "处理中…" : "删除", `删除 ${job.filename} 及其文件`, changing)); row.append(actions); fragment.append(row);
    }
    ui["history-list"].replaceChildren(fragment); ui["history-empty"].hidden = jobs.length > 0;
    ui["history-count"].textContent = String(jobs.length); ui["refresh-button"].disabled = jobsBusy;
    ui["archive-button"].disabled = !archiveIds.size || archiveBusy; ui["archive-button"].textContent = archiveBusy ? "正在打包…" : "下载 ZIP";
    ui["archive-count"].textContent = archiveIds.size ? `已选择 ${archiveIds.size} / 10 个文件` : "勾选已完成文件，最多 10 个";
  }

  function renderDocument() {
    const job = selectedJob(), state = job ? status(job) : null;
    const done = state === "succeeded" && selectedDocument && String(selectedDocument.id) === selectedId;
    const hasText = done && selectedDocument.markdown.length > 0;
    ui["copy-button"].disabled = !done; ui["download-button"].disabled = !done;
    ui["document-heading"].hidden = !job; ui["document-name"].textContent = job?.filename || "";
    ui["document-length"].textContent = done ? `${selectedDocument.markdown.length.toLocaleString("zh-CN")} 字符` : "";
    ui["document-expiry"].textContent = job ? `${utc(job.expires_at)} 到期` : "请及时下载需要保留的内容";
    ui["output-label"].textContent = detailLoading ? "正在读取" : statusNames[state] || "等待选择";
    ui["output-empty"].hidden = !!hasText; ui["preview-panel"].hidden = !hasText || view !== "preview"; ui["source-panel"].hidden = !hasText || view !== "source";
    for (const mode of ["preview", "source"]) { ui[`${mode}-tab`].setAttribute("aria-selected", String(view === mode)); ui[`${mode}-tab`].tabIndex = view === mode ? 0 : -1; }
    if (done && renderedDocument !== selectedDocument) {
      // This is the only HTML insertion: the authenticated API sanitizes preview HTML.
      ui["markdown-preview"].innerHTML = selectedDocument.html;
      ui["markdown-source"].value = selectedDocument.markdown; ui["markdown-source"].scrollTop = 0; ui["preview-panel"].scrollTop = 0; renderedDocument = selectedDocument;
    } else if (!done) { ui["markdown-preview"].replaceChildren(); ui["markdown-source"].value = ""; renderedDocument = null; }
    let title = "下一站，Markdown", description = "上传文件，或选择一条转换记录\n在这里预览结果，或查看 Markdown 源码";
    if (detailLoading) { title = "正在读取结果…"; description = "正在加载你的转换内容"; }
    else if (state === "expired") { title = "文件已过期"; description = "原文件与结果从上传起保留 24 小时\n如需再次转换，请重新上传文件"; }
    else if (state === "failed") { title = "这个文件暂时无法转换"; description = job.error || "可在转换记录中重试"; }
    else if (state === "queued" || state === "running") { title = state === "queued" ? "已加入转换队列" : "正在整理文字…"; description = "服务会自动更新进度\n你也可以离开页面，稍后登录查看"; }
    else if (detailError) { title = "结果暂时无法读取"; description = `${detailError}\n选择这条记录可重新读取`; }
    else if (done && !hasText) { title = "没有可预览的文字"; description = "转换已完成，但结果为空\n请确认文档包含可提取的文字"; }
    ui["empty-title"].textContent = title; ui["empty-description"].textContent = description;
  }

  function render() {
    const active = document.activeElement, focusedAction = active?.dataset.action, focusedId = active?.dataset.id;
    renderAuth(); renderQueue(); renderHistory(); renderDocument();
    if (focusedAction && focusedId) {
      const replacement = [...ui["history-list"].querySelectorAll("[data-action]"), ...ui["file-list"].querySelectorAll("[data-action]")].find((node) => node.dataset.action === focusedAction && node.dataset.id === focusedId);
      if (replacement && !replacement.disabled) replacement.focus({ preventScroll: true });
    }
  }

  function scheduleTimers() {
    clearTimeout(pollTimer); clearTimeout(expiryTimer); pollTimer = expiryTimer = null;
    if (!session) return;
    if (jobs.some((job) => ["queued", "running"].includes(status(job)))) pollTimer = setTimeout(() => refreshJobs(), 2000);
    const next = jobs.map((job) => job.expires_at * 1000 - Date.now()).filter((delay) => delay > 0);
    if (next.length) expiryTimer = setTimeout(() => { if (!session) return; if (selectedJob() && status(selectedJob()) === "expired") clearDocument(); render(); scheduleTimers(); }, Math.min(...next, 2147483000) + 20);
  }

  function clearDocument() { detailSequence += 1; detailController?.abort(); detailController = null; selectedDocument = null; detailLoading = false; detailError = ""; }
  async function selectJob(id) {
    clearDocument(); selectedId = String(id); const job = selectedJob(); render();
    if (!session || !job || status(job) !== "succeeded") return;
    const seq = detailSequence, currentEpoch = epoch;
    detailController = new AbortController(); detailLoading = true; renderDocument();
    try {
      const data = await request(`/api/jobs/${encodeURIComponent(id)}`, { signal: detailController.signal });
      if (seq !== detailSequence || epoch !== currentEpoch || selectedId !== String(id)) return;
      if (String(data.id) !== selectedId || typeof data.markdown !== "string" || typeof data.html !== "string" || data.status !== "succeeded") throw new Error("服务返回的结果不完整，请刷新重试。");
      selectedDocument = data;
    } catch (error) { if (!stale(error) && seq === detailSequence && epoch === currentEpoch) detailError = message(error); }
    finally { if (seq === detailSequence && epoch === currentEpoch) { detailLoading = false; renderDocument(); } }
  }

  async function refreshJobs() {
    if (!session || jobsBusy) return;
    const seq = ++jobsSequence, currentEpoch = epoch; jobsBusy = true; clearTimeout(pollTimer); render();
    try {
      const data = await request("/api/jobs");
      if (seq !== jobsSequence || epoch !== currentEpoch) return;
      if (!Array.isArray(data.jobs) || !data.usage) throw new Error("转换记录响应不完整，请重试。");
      jobs = data.jobs; usage = data.usage; validatePending();
      for (const id of archiveIds) if (!jobs.some((job) => String(job.id) === id && status(job) === "succeeded")) archiveIds.delete(id);
      const selected = selectedJob();
      if (!selected) { selectedId = null; clearDocument(); }
      else if (status(selected) !== "succeeded") clearDocument();
      else if (!selectedDocument && !detailLoading && !detailError) void selectJob(selectedId);
      ui["history-note"].textContent = "仅展示当前账户最近 100 条记录 · 从上传起保留 24 小时";
    } catch (error) { if (!stale(error) && epoch === currentEpoch && seq === jobsSequence) ui["history-note"].textContent = `暂未刷新：${message(error)}`; }
    finally { if (seq === jobsSequence && epoch === currentEpoch) { jobsBusy = false; render(); scheduleTimers(); } }
  }

  function validatePending() {
    let total = 0;
    for (const entry of pending) {
      const file = entry.file; entry.error = "";
      if (!config.extensions.includes(extension(file.name))) entry.error = "暂不支持此文件格式";
      else if (!file.size) entry.error = "文件为空，请选择包含内容的文件";
      else if (file.size > fileLimit()) entry.error = `单文件不能超过 ${bytes(fileLimit())}`;
      else if (total + file.size > config.max_total_bytes) entry.error = `本批文件总计不能超过 ${bytes(config.max_total_bytes)}`;
      if (!entry.error) total += file.size;
    }
  }
  function addFiles(files) {
    if (!session || !ready || uploadBusy) return;
    const warnings = [];
    for (const file of files) {
      if (pending.length >= config.max_files) { warnings.push(`文件数量已达上限（${config.max_files} 个）`); break; }
      if (pending.some((entry) => entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified)) { warnings.push(`已跳过重复文件：${file.name}`); continue; }
      pending.push({ id: nextFileId++, file, error: "" });
    }
    validatePending(); ui["file-input"].value = ""; notice(warnings.join("\n")); render();
  }
  async function upload() {
    if (!session || uploadBusy || !ready) return;
    validatePending(); const entries = pending.filter((entry) => !entry.error); if (!entries.length) { render(); return; }
    if (usage && entries.length > usage.remaining) { notice(`今日还可上传 ${usage.remaining} 个文件，请减少待上传文件。`); return; }
    const currentEpoch = epoch; uploadBusy = true; jobsSequence += 1; jobsBusy = false; clearTimeout(pollTimer); notice(""); render();
    const form = new FormData(); for (const entry of entries) form.append("files", entry.file);
    try {
      const data = await request("/api/jobs", { method: "POST", body: form });
      if (!Array.isArray(data.jobs) || !Array.isArray(data.errors)) throw new Error("上传响应不完整。请先刷新记录确认是否已接收，再重新上传，避免重复计入额度。");
      jobsSequence += 1; jobsBusy = false;
      const ids = new Set(entries.map((entry) => entry.id)); pending = pending.filter((entry) => !ids.has(entry.id));
      jobs = [...data.jobs, ...jobs.filter((job) => !data.jobs.some((added) => String(added.id) === String(job.id)))].slice(0, 100);
      if (data.jobs.length) { selectedId = String(data.jobs[0].id); clearDocument(); announce(`${data.jobs.length} 个文件已加入转换队列`); }
      if (data.errors.length) notice(data.errors.map((entry) => `${entry.filename}：${entry.error}`).join("\n"));
      await refreshJobs();
    } catch (error) { if (!stale(error) && epoch === currentEpoch) { notice(message(error)); void refreshJobs(); } }
    finally { if (epoch === currentEpoch) { uploadBusy = false; render(); scheduleTimers(); } }
  }

  async function mutateJob(action, id) {
    if (!session || jobMutations.has(id)) return;
    const job = jobs.find((item) => String(item.id) === id); if (!job) return;
    if (action === "retry" && (status(job) !== "failed" || job.attempts >= 3 || usage?.remaining === 0)) return;
    if (action === "delete" && !window.confirm(`删除「${job.filename}」？原文件、转换结果和记录将立即删除，无法恢复。`)) return;
    const currentEpoch = epoch; jobMutations.add(id); jobsSequence += 1; jobsBusy = false; clearTimeout(pollTimer); render(); notice("");
    try {
      const data = await request(`/api/jobs/${encodeURIComponent(id)}${action === "retry" ? "/retry" : ""}`, { method: action === "retry" ? "POST" : "DELETE" });
      jobsSequence += 1; jobsBusy = false;
      if (action === "delete") {
        jobs = jobs.filter((item) => String(item.id) !== id); archiveIds.delete(id);
        if (selectedId === id) { selectedId = null; clearDocument(); }
        announce("已删除文件与记录");
      } else {
        jobs = jobs.map((item) => String(item.id) === id ? data : item); selectedId = id; clearDocument(); announce("已重新加入转换队列");
      }
      await refreshJobs();
    } catch (error) { if (!stale(error) && epoch === currentEpoch) { notice(message(error)); void refreshJobs(); } }
    finally { if (epoch === currentEpoch) { jobMutations.delete(id); render(); scheduleTimers(); } }
  }

  function downloadLink(href, filename) { const link = element("a"); link.href = href; link.download = filename; document.body.append(link); link.click(); link.remove(); }
  async function archive() {
    if (!session || archiveBusy) return;
    const selected = jobs.filter((job) => archiveIds.has(String(job.id)) && status(job) === "succeeded").map((job) => job.id);
    if (!selected.length || selected.length > 10) return;
    const currentEpoch = epoch; archiveBusy = true; render();
    try {
      const blob = await request("/api/jobs/archive", { method: "POST", body: { job_ids: selected }, blob: true });
      const url = URL.createObjectURL(blob); objectUrls.add(url); downloadLink(url, "markitdown-results.zip");
      setTimeout(() => { URL.revokeObjectURL(url); objectUrls.delete(url); }, 1000); announce("ZIP 下载已准备好");
    } catch (error) { if (!stale(error) && epoch === currentEpoch) notice(message(error)); }
    finally { if (epoch === currentEpoch) { archiveBusy = false; render(); } }
  }
  async function copy(text, success) {
    const currentEpoch = epoch;
    try { await navigator.clipboard.writeText(text); if (epoch === currentEpoch) announce(success); }
    catch { if (epoch === currentEpoch) notice("浏览器未允许复制。请切换源码或选中邀请码，手动复制。"); }
  }

  function setAuthMode(mode) { if (authBusy) return; authMode = mode; authMessage(""); renderAuth(); }
  function establishSession(data) {
    if (!data.user || typeof data.csrf_token !== "string" || !data.usage) throw new Error("登录响应不完整，请重试。");
    resetSession(); session = data; usage = data.usage; authMessage(""); render(); void refreshJobs();
  }
  async function authenticate(mode) {
    if (!ready || !config.has_admin || authBusy || session || logoutToken !== null) return;
    const username = ui[`${mode}-username`].value.trim(), password = ui[`${mode}-password`].value;
    if (!/^[a-z0-9_.-]{3,32}$/.test(username)) { authMessage("用户名需为 3–32 位小写字母、数字、下划线、点或短横线。", true); return; }
    if (password.length < 12 || password.length > 128) { authMessage("密码长度需为 12–128 个字符。", true); return; }
    const token = ui["invite-token"].value.trim(); if (mode === "register" && !token) { authMessage("请填写管理员提供的邀请码。", true); return; }
    const currentEpoch = epoch; authBusy = true; authMessage(mode === "register" ? "正在创建账户…" : "正在登录…"); renderAuth();
    try {
      const data = await request(`/api/auth/${mode}`, { method: "POST", body: { username, password, ...(mode === "register" ? { invite_token: token } : {}) }, auth: false });
      ui[`${mode}-password`].value = "";
      if (mode === "register") { ui["invite-token"].value = ""; ui["register-username"].value = ""; ui["login-username"].value = username; authMode = "login"; authMessage("账户已创建。请用刚才设置的密码登录。"); renderAuth(); ui["login-password"].focus(); }
      else establishSession(data);
    } catch (error) { if (!stale(error) && epoch === currentEpoch) authMessage(message(error), true); }
    finally { if (epoch === currentEpoch) { authBusy = false; renderAuth(); } }
  }

  async function finishLogout() {
    if (logoutToken === null || logoutBusy) return;
    const token = logoutToken, expectedUser = logoutUserId, currentEpoch = epoch; logoutBusy = true; renderAuth();
    try {
      const response = await fetch("/api/auth/logout", { method: "POST", headers: { "X-MarkItDown-Request": "1", "X-CSRF-Token": token }, credentials: "same-origin", cache: "no-store" });
      if (epoch !== currentEpoch) return;
      const responseUser = response.headers.get("X-MarkItDown-User");
      if (response.status === 403 || (responseUser !== null && responseUser !== expectedUser)) {
        sessionChanged("其他页面可能已切换账户。页面内容已清空，请重新连接确认当前账户后再退出。");
        return;
      }
      if (!response.ok && response.status !== 401) throw new Error("退出尚未由服务确认，请重试退出。");
      logoutToken = logoutUserId = null; authMessage("已退出登录，页面中的文件与结果已清空。");
    } catch { if (epoch === currentEpoch) authMessage("页面已清空，但服务尚未确认退出。请重试退出，尤其是在共用电脑上。", true); }
    finally { if (epoch === currentEpoch) { logoutBusy = false; renderAuth(); } }
  }
  function logout() { if (!session) return; const token = session.csrf_token, userId = String(session.user.id); resetSession(); logoutToken = token; logoutUserId = userId; authMode = "login"; authMessage("正在退出登录…"); renderAuth(); void finishLogout(); }

  async function verifySession() {
    if (!session || sessionRefreshBusy) return;
    const currentEpoch = epoch; sessionRefreshBusy = true;
    try {
      const data = await request("/api/me");
      if (!data.user || String(data.user.id) !== String(session.user.id) || typeof data.csrf_token !== "string" || !data.usage) throw new Error("账户信息不完整，请重新连接。");
      session = data; usage = data.usage; validatePending(); render();
    } catch (error) { if (!stale(error) && currentEpoch === epoch) notice(message(error)); }
    finally { if (currentEpoch === epoch) sessionRefreshBusy = false; }
  }

  async function connect() {
    if (connecting) return;
    connecting = true; ready = false; ui["reconnect-button"].disabled = true; render(); const currentEpoch = epoch, seq = ++connectionSequence;
    try {
      const data = await request("/api/config", { auth: false });
      for (const key of ["max_files", "max_file_bytes", "max_total_bytes"]) if (!Number.isSafeInteger(data[key]) || data[key] < 1) throw new Error("服务配置无效，请联系管理员。");
      if (!Array.isArray(data.extensions) || typeof data.has_admin !== "boolean") throw new Error("服务配置不完整，请联系管理员。");
      const extensions = [...new Set(data.extensions.filter((value) => typeof value === "string").map((value) => `${value.startsWith(".") ? "" : "."}${value.toLowerCase()}`))].filter((value) => defaults.extensions.includes(value));
      if (!extensions.length) throw new Error("服务没有可用的文件格式。");
      config = { ...data, extensions, max_files: Math.min(data.max_files, defaults.max_files), max_file_bytes: Math.min(data.max_file_bytes, defaults.max_file_bytes), max_total_bytes: Math.min(data.max_total_bytes, defaults.max_total_bytes) };
      ready = true; ui["connection-notice"].hidden = true; ui["file-input"].accept = extensions.join(",");
      ui["format-list"].replaceChildren(...extensions.map((value) => element("span", "", value.slice(1).toUpperCase())));
      if (!session && logoutToken === null && config.has_admin) {
        try { establishSession(await request("/api/me", { auth: false })); }
        catch (error) { if (!stale(error) && error.status !== 401) authMessage(message(error), true); else if (error.status === 401) authMessage("欢迎回来，登录后继续整理资料。"); }
      } else if (!config.has_admin) authMessage("请先由服务所有者完成管理员初始化。");
    } catch (error) { if (!stale(error) && epoch === currentEpoch) { ready = false; ui["connection-message"].textContent = message(error); ui["connection-notice"].hidden = false; authMessage("服务暂未连接，请重新连接后继续。", true); } }
    finally { if (seq === connectionSequence) { connecting = false; ui["reconnect-button"].disabled = false; validatePending(); render(); } }
  }

  function renderInvites(invites) {
    const fragment = document.createDocumentFragment();
    for (const invite of invites) {
      const row = element("li", "admin-card"), heading = element("div", "admin-card-heading");
      const used = Boolean(invite.used_at || invite.used_by || invite.is_used), revoked = Boolean(invite.revoked_at || invite.is_revoked), expired = invite.expires_at * 1000 <= Date.now();
      heading.append(element("span", "", `邀请码 #${invite.id ?? "—"}`), element("span", "", used ? "已使用" : revoked ? "已撤销" : expired ? "已过期" : "可使用"));
      row.append(heading, element("p", "", `${utc(invite.expires_at)} 到期`));
      if (!used && !revoked && !expired && invite.id !== undefined) row.append(actionButton("revoke", invite.id, "撤销", `撤销邀请码 ${invite.id}`));
      fragment.append(row);
    }
    if (!invites.length) fragment.append(element("li", "field-help", "还没有邀请码")); ui["invites-list"].replaceChildren(fragment);
  }
  function renderUsers(users) {
    const fragment = document.createDocumentFragment();
    for (const user of users) {
      const row = element("li", "admin-card"), form = element("form"); form.dataset.userId = String(user.id);
      const heading = element("div", "admin-card-heading"); heading.append(element("span", "", user.username), element("span", "", user.is_admin ? "管理员" : "成员"));
      form.append(heading); const fields = element("div", "user-fields");
      for (const [key, label, value, max] of [["daily_quota", "每日文件额度", user.daily_quota, 1000], ["max_file_mib", "单文件上限（MiB）", user.max_file_bytes / 1048576, 20]]) {
        const wrap = element("div"), id = `user-${user.id}-${key}`, labelNode = element("label", "", label); labelNode.htmlFor = id;
        const input = element("input"); input.id = id; input.name = key; input.type = "number"; input.min = "1"; input.max = String(max); input.step = key === "daily_quota" ? "1" : "any"; input.required = true; input.value = String(value); input.dataset.field = key;
        wrap.append(labelNode, input); fields.append(wrap);
      }
      const controls = element("div", "user-controls"), activeLabel = element("label"), active = element("input"); active.type = "checkbox"; active.checked = user.is_active; active.dataset.field = "is_active";
      activeLabel.append(active, element("span", "", "允许登录")); const save = element("button", "choose-button", "保存设置"); save.type = "submit"; save.setAttribute("aria-label", `保存 ${user.username} 的账户设置`);
      controls.append(activeLabel, save); form.append(fields, controls); row.append(form); fragment.append(row);
    }
    ui["users-list"].replaceChildren(fragment);
  }
  async function refreshAdmin() {
    if (!session?.user.is_admin || adminBusy || ui["admin-panel"].hidden) return;
    const currentEpoch = epoch, seq = ++adminSequence; adminBusy = true; ui["admin-refresh"].disabled = true; adminMessage("正在读取管理信息…");
    try {
      const [invites, users] = await Promise.all([request("/api/admin/invites"), request("/api/admin/users")]);
      if (seq !== adminSequence || currentEpoch !== epoch) return;
      if (!Array.isArray(invites.invites) || !Array.isArray(users.users)) throw new Error("管理信息不完整，请重试。");
      renderInvites(invites.invites); renderUsers(users.users); adminMessage("");
    } catch (error) { if (!stale(error) && currentEpoch === epoch) adminMessage(message(error), true); }
    finally { if (currentEpoch === epoch && seq === adminSequence) { adminBusy = false; ui["admin-refresh"].disabled = false; } }
  }
  async function createInvite() {
    if (!session?.user.is_admin || inviteBusy) return;
    const currentEpoch = epoch; inviteBusy = true; ui["invite-create"].disabled = true; clearInvite();
    try {
      const data = await request("/api/admin/invites", { method: "POST", body: { ttl_hours: 24 } });
      if (ui["admin-panel"].hidden) return;
      if (typeof data.token !== "string") throw new Error("邀请码响应不完整，请刷新列表后重试。");
      ui["new-invite-token"].value = data.token; ui["invite-expiry"].textContent = `${utc(data.expires_at)} 到期 · 一次性使用`; ui["invite-result"].hidden = false;
      adminMessage("邀请码已生成。仅分享给你希望加入的成员。"); adminSequence += 1; adminBusy = false; await refreshAdmin();
    } catch (error) { if (!stale(error) && epoch === currentEpoch) adminMessage(message(error), true); }
    finally { if (epoch === currentEpoch) { inviteBusy = false; ui["invite-create"].disabled = false; } }
  }
  async function revokeInvite(button) {
    if (!session?.user.is_admin || button.disabled) return;
    const id = button.dataset.id; if (!window.confirm(`撤销邀请码 #${id}？未使用的邀请将无法再注册。`)) return;
    const currentEpoch = epoch; button.disabled = true;
    try { await request(`/api/admin/invites/${encodeURIComponent(id)}`, { method: "DELETE" }); clearInvite(); adminSequence += 1; adminBusy = false; await refreshAdmin(); }
    catch (error) { if (!stale(error) && epoch === currentEpoch) { adminMessage(message(error), true); button.disabled = false; } }
  }
  async function saveUser(form) {
    if (!session?.user.is_admin || form.dataset.saving === "true") return;
    const fields = [...form.querySelectorAll("[data-field]")], field = (name) => fields.find((node) => node.dataset.field === name);
    const quota = Number(field("daily_quota").value), mib = Number(field("max_file_mib").value), isActive = field("is_active").checked, id = form.dataset.userId;
    if (!Number.isInteger(quota) || quota < 1 || quota > 1000 || !Number.isFinite(mib) || mib < 1 || mib > 20) { adminMessage("每日额度须为 1–1000 的整数，单文件上限须为 1–20 MiB。", true); return; }
    const currentEpoch = epoch; form.dataset.saving = "true"; const submit = form.querySelector("button"); submit.disabled = true;
    try {
      await request(`/api/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: { daily_quota: quota, max_file_bytes: Math.floor(mib * 1048576), is_active: isActive } });
      adminMessage("账户设置已保存。"); if (String(session.user.id) === id) { const data = await request("/api/me"); session = data; usage = data.usage; validatePending(); render(); }
      adminSequence += 1; adminBusy = false; await refreshAdmin();
    } catch (error) { if (!stale(error) && epoch === currentEpoch) adminMessage(message(error), true); }
    finally { if (epoch === currentEpoch) { form.dataset.saving = "false"; submit.disabled = false; } }
  }

  ui["login-form"].addEventListener("submit", (event) => { event.preventDefault(); void authenticate("login"); });
  ui["register-form"].addEventListener("submit", (event) => { event.preventDefault(); void authenticate("register"); });
  ui["login-tab"].addEventListener("click", () => setAuthMode("login")); ui["register-tab"].addEventListener("click", () => setAuthMode("register"));
  for (const mode of ["login", "register"]) ui[`${mode}-tab`].addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = event.key === "Home" ? "login" : event.key === "End" ? "register" : mode === "login" ? "register" : "login"; setAuthMode(next); ui[`${next}-tab`].focus(); });
  ui["logout-button"].addEventListener("click", logout); ui["logout-retry"].addEventListener("click", finishLogout); ui["reconnect-button"].addEventListener("click", connect);
  ui["choose-button"].addEventListener("click", () => ui["file-input"].click()); ui["file-input"].addEventListener("change", () => addFiles(Array.from(ui["file-input"].files || [])));
  ui["clear-button"].addEventListener("click", () => { if (uploadBusy) return; pending = []; ui["file-input"].value = ""; notice(""); render(); });
  ui["convert-button"].addEventListener("click", upload); ui["refresh-button"].addEventListener("click", refreshJobs); ui["archive-button"].addEventListener("click", archive);
  ui["file-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (!button || uploadBusy) return; pending = pending.filter((entry) => String(entry.id) !== button.dataset.id); validatePending(); render(); });
  ui["history-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (!button || button.disabled) return; const { action, id } = button.dataset; if (action === "select") void selectJob(id); else void mutateJob(action, id); });
  ui["history-list"].addEventListener("change", (event) => { const input = event.target; if (input.dataset.action !== "archive" || input.disabled) return; if (input.checked && archiveIds.size >= 10) { input.checked = false; notice("ZIP 每次最多选择 10 个文件。"); } else if (input.checked) archiveIds.add(input.dataset.id); else archiveIds.delete(input.dataset.id); renderHistory(); });
  for (const mode of ["preview", "source"]) {
    ui[`${mode}-tab`].addEventListener("click", () => { view = mode; renderDocument(); });
    ui[`${mode}-tab`].addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); view = event.key === "Home" ? "preview" : event.key === "End" ? "source" : view === "preview" ? "source" : "preview"; renderDocument(); ui[`${view}-tab`].focus(); });
  }
  ui["copy-button"].addEventListener("click", () => { if (selectedDocument && status(selectedJob()) === "succeeded") void copy(selectedDocument.markdown, "Markdown 已复制"); });
  ui["download-button"].addEventListener("click", () => { const job = selectedJob(); if (job && selectedDocument && status(job) === "succeeded") downloadLink(`/api/jobs/${encodeURIComponent(job.id)}/download`, ""); });
  ui["drop-zone"].addEventListener("dragenter", (event) => { event.preventDefault(); if (!session || uploadBusy) return; dragDepth += 1; ui["drop-zone"].classList.add("drag-over"); });
  ui["drop-zone"].addEventListener("dragover", (event) => event.preventDefault());
  ui["drop-zone"].addEventListener("dragleave", (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) ui["drop-zone"].classList.remove("drag-over"); });
  ui["drop-zone"].addEventListener("drop", (event) => { event.preventDefault(); dragDepth = 0; ui["drop-zone"].classList.remove("drag-over"); addFiles(Array.from(event.dataTransfer?.files || [])); });
  document.addEventListener("dragover", (event) => event.preventDefault()); document.addEventListener("drop", (event) => event.preventDefault());
  ui["admin-toggle"].addEventListener("click", () => { if (!session?.user.is_admin) return; ui["admin-panel"].hidden = !ui["admin-panel"].hidden; ui["admin-toggle"].setAttribute("aria-expanded", String(!ui["admin-panel"].hidden)); if (!ui["admin-panel"].hidden) void refreshAdmin(); else clearInvite(); });
  ui["admin-refresh"].addEventListener("click", refreshAdmin); ui["invite-create"].addEventListener("click", createInvite); ui["invite-dismiss"].addEventListener("click", clearInvite);
  ui["invite-copy"].addEventListener("click", () => { if (session?.user.is_admin && ui["new-invite-token"].value) void copy(ui["new-invite-token"].value, "邀请码已复制"); });
  ui["invites-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (button?.dataset.action === "revoke") void revokeInvite(button); });
  ui["users-list"].addEventListener("submit", (event) => { event.preventDefault(); if (event.target.tagName.toLowerCase() === "form") void saveUser(event.target); });
  window.addEventListener("focus", verifySession);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void verifySession(); });
  window.addEventListener("pagehide", () => { resetSession(); authMessage("返回页面后会重新验证登录状态。"); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) void connect(); });
  render(); void connect();
})();
