/* Same-origin, invitation-only workspace. Sessions and document previews stay in memory. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "auth-section", "session-section", "bootstrap-notice", "login-tab", "register-tab", "login-form", "register-form",
    "login-username", "login-password", "register-username", "register-password", "invite-token", "login-button", "register-button", "auth-message",
    "logout-button", "logout-retry", "account-name", "account-role", "quota-summary", "quota-reset", "connection-notice", "connection-message", "reconnect-button", "notice",
    "file-input", "choose-button", "drop-zone", "clear-button", "convert-button", "convert-label", "file-list", "queue-empty", "queue-count", "queue-size", "conversion-summary", "upload-limits", "format-list", "format-note", "upload-progress", "upload-recheck", "workspace-status",
    "engine-group", "engine-markitdown", "engine-docling", "engine-card-markitdown", "engine-card-docling", "engine-badge-markitdown", "engine-badge-docling", "engine-limit-markitdown", "engine-limit-docling", "engine-selection-note",
    "history-list", "history-count", "history-empty", "history-note", "refresh-button", "archive-button", "archive-count", "filter-all", "filter-active", "filter-completed", "filter-failed",
    "preview-tab", "source-tab", "split-tab", "preview-panel", "source-panel", "markdown-preview", "markdown-source", "output-content", "output-empty", "empty-title", "empty-description", "document-heading", "document-name", "document-length", "document-engine", "document-expiry", "document-progress", "document-reload", "document-retry", "document-cancel", "preview-safety", "output-label", "copy-button", "download-button", "action-status",
    "auth-retention", "account-retention", "result-review", "job-facts", "job-facts-list", "attempt-list", "manifest-download",
    "invite-hours", "user-defaults", "settings-form", "settings-version", "setting-daily-quota", "setting-file-mib", "setting-retention-hours", "setting-quota-help", "setting-file-help", "setting-retention-help", "settings-save", "settings-status", "service-config", "audit-list", "audit-note", "audit-refresh",
    "admin-toggle", "admin-panel", "admin-refresh", "admin-status", "invite-create", "invite-result", "new-invite-token", "invite-expiry", "invite-copy", "invite-dismiss", "invites-list", "users-list",
  ].map((id) => [id, $(id)]));
  const defaults = { max_files: 10, max_file_bytes: 20 * 1024 * 1024, max_total_bytes: 50 * 1024 * 1024, extensions: [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"] };
  const statusNames = { queued: "排队中", running: "转换中…", succeeded: "已完成", failed: "转换失败", expired: "已过期" };
  let config = { ...defaults, has_admin: false }, ready = false, connecting = false, connectionSequence = 0;
  let session = null, usage = null, epoch = 0, authBusy = false, authMode = "login", logoutToken = null, logoutUserId = null, logoutBusy = false;
  let pending = [], nextFileId = 1, jobs = [], selectedId = null, selectedDocument = null, view = "preview", renderedDocument = null;
  let uploadBusy = false, uploadUncertain = false, archiveBusy = false, jobsBusy = false, jobsSequence = 0, detailSequence = 0, detailLoading = false, detailError = "", detailController = null;
  let selectedEngine = "markitdown", historyFilter = "all", uploadKey = null, uploadPayload = null;
  let adminSettings = null, settingsBusy = false, settingsDirty = false, auditBusy = false, auditSequence = 0;
  let pollTimer = null, expiryTimer = null, dragDepth = 0, adminSequence = 0, adminBusy = false, inviteBusy = false, sessionRefreshBusy = false;
  const controllers = new Set(), objectUrls = new Set(), archiveIds = new Set(), jobMutations = new Set(), retryIntents = new Map();

  class StaleResponse extends Error {}
  function stale(error) { return error instanceof StaleResponse || error.name === "AbortError"; }
  function message(error) { return error instanceof TypeError ? "无法连接服务，请检查网络后重试。" : error.message || "操作未完成，请重试。"; }
  function notice(text) { ui.notice.textContent = text; ui.notice.hidden = !text; }
  function announce(text) { ui["action-status"].textContent = text; }
  function authMessage(text, error = false) { ui["auth-message"].textContent = text; ui["auth-message"].classList.toggle("is-error", error); }
  function adminMessage(text, error = false) { ui["admin-status"].textContent = text; ui["admin-status"].classList.toggle("is-error", error); }
  function bytes(value) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1).replace(/\.0$/, "")} KiB` : `${(value / 1048576).toFixed(1).replace(/\.0$/, "")} MiB`; }
  function utc(value) { if (value === null || value === undefined || value === "") return "时间未知"; const date = new Date(Number(value) * 1000); return Number.isFinite(date.getTime()) ? `${date.toISOString().slice(0, 16).replace("T", " ")} UTC` : "时间未知"; }
  function localTime(value) {
    if (value === null || value === undefined || value === "") return "本地时间未知";
    const date = new Date(Number(value) * 1000);
    return Number.isFinite(date.getTime()) ? `${date.toLocaleString("zh-CN", { hour12: false, timeZoneName: "short" })}（本地）` : "本地时间未知";
  }
  function duration(seconds) { return Number.isFinite(Number(seconds)) ? `${Number((Number(seconds) / 3600).toFixed(2))} 小时` : "由服务决定"; }
  function retention() { return duration(config.retention_seconds); }
  function attemptLimit() { return Number.isSafeInteger(usage?.max_attempts) && usage.max_attempts > 0 ? usage.max_attempts : 3; }
  function adoptUsage(value) {
    usage = value;
    if (Number.isFinite(value?.retention_seconds) && value.retention_seconds > 0) config.retention_seconds = value.retention_seconds;
    if (Number.isSafeInteger(value?.config_version)) config.config_version = value.config_version;
  }
  function newIdempotencyKey() {
    if (!globalThis.crypto?.getRandomValues) throw new Error("浏览器缺少安全随机数支持，无法安全提交。请使用较新的浏览器。");
    const buffer = new Uint8Array(24); globalThis.crypto.getRandomValues(buffer);
    return Array.from(buffer, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  function resetUploadIntent() { uploadKey = null; uploadPayload = null; uploadUncertain = false; }
  function inviteError(error) {
    const states = { invite_invalid: "邀请码无效，请检查输入或联系邀请你的管理员。", invite_expired: "邀请码已过期，请联系邀请你的管理员获取新邀请。", invite_used: "邀请码已使用；如果你已注册，请切换登录，否则联系管理员。", invite_revoked: "邀请码已撤销，请联系邀请你的管理员。" };
    return states[error.code] || message(error);
  }
  function extension(name) { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); }
  function status(job) { return job.expires_at && job.expires_at * 1000 <= Date.now() ? "expired" : job.status; }
  function selectedJob() { return jobs.find((job) => String(job.id) === selectedId); }
  function engineInfo(id = selectedEngine) {
    const data = Array.isArray(config.engines) ? config.engines.find((engine) => engine.id === id) : null;
    const enhanced = id === "docling";
    return {
      id, label: enhanced ? "Docling" : "MarkItDown", available: enhanced ? data?.available === true : data?.available !== false,
      reason: typeof data?.reason === "string" ? data.reason : enhanced ? "此服务尚未启用 Docling，仍可使用默认引擎" : "默认引擎暂不可用，请联系管理员",
      max_file_bytes: Number.isSafeInteger(data?.max_file_bytes) && data.max_file_bytes > 0 ? Math.min(data.max_file_bytes, enhanced ? 10 * 1048576 : config.max_file_bytes) : enhanced ? 10 * 1048576 : config.max_file_bytes,
      max_pages: Number.isSafeInteger(data?.max_pages) && data.max_pages > 0 ? enhanced ? Math.min(data.max_pages, 2) : data.max_pages : enhanced ? 2 : null,
      timeout_seconds: Number.isSafeInteger(data?.timeout_seconds) && data.timeout_seconds > 0 ? data.timeout_seconds : enhanced ? 60 : 45,
    };
  }
  function fileLimit() { return Math.min(config.max_file_bytes, usage?.max_file_bytes || session?.user.max_file_bytes || config.max_file_bytes, engineInfo().max_file_bytes); }
  function jobStatusName(job) { return job.lifecycle_status === "stopping" ? "正在停止" : status(job) === "failed" && job.error === "任务已取消" ? "已取消" : statusNames[status(job)] || "未知状态"; }
  function isActive(job) { return ["queued", "running"].includes(status(job)); }
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function actionButton(action, id, text, label, disabled = false) { const node = element("button", "file-action", text); node.type = "button"; node.dataset.action = action; node.dataset.id = String(id); node.setAttribute("aria-label", label); node.disabled = disabled; return node; }
  function revokeUrls() { for (const url of objectUrls) URL.revokeObjectURL(url); objectUrls.clear(); }
  function clearInvite() { ui["new-invite-token"].value = ""; ui["invite-expiry"].textContent = ""; ui["invite-result"].hidden = true; }

  async function request(path, { method = "GET", body, auth = true, signal, blob = false, idempotencyKey } = {}) {
    const requestEpoch = epoch;
    const controller = new AbortController();
    controllers.add(controller);
    const relayAbort = () => controller.abort();
    if (signal) { if (signal.aborted) controller.abort(); else signal.addEventListener("abort", relayAbort, { once: true }); }
    const headers = {};
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
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
        const error = new Error(typeof data.detail === "string" ? data.detail : `请求未完成（${response.status}）`); error.status = response.status; error.code = data.code || data.error_code;
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
    revokeUrls(); session = usage = null; pending = []; jobs = []; archiveIds.clear(); jobMutations.clear(); retryIntents.clear();
    selectedId = null; selectedDocument = renderedDocument = null; detailError = ""; detailLoading = false;
    uploadBusy = uploadUncertain = archiveBusy = jobsBusy = authBusy = adminBusy = inviteBusy = sessionRefreshBusy = false;
    selectedEngine = "markitdown"; historyFilter = "all"; resetUploadIntent(); adminSettings = null; settingsBusy = settingsDirty = auditBusy = false; auditSequence += 1; ui["audit-refresh"].disabled = false;
    logoutToken = logoutUserId = null; logoutBusy = false; dragDepth = 0; view = "preview";
    ui["file-input"].value = "";
    for (const id of ["login-username", "login-password", "register-username", "register-password", "invite-token"]) ui[id].value = "";
    ui["drop-zone"].classList.remove("drag-over"); ui["admin-panel"].hidden = true;
    ui["admin-toggle"].setAttribute("aria-expanded", "false"); ui["admin-refresh"].disabled = false; ui["invite-create"].disabled = false; ui["users-list"].replaceChildren(); ui["invites-list"].replaceChildren();
    ui["service-config"].replaceChildren(); ui["audit-list"].replaceChildren(); ui["settings-status"].textContent = ""; ui["settings-version"].textContent = ""; settingsFieldsDisabled(true);
    for (const id of ["setting-daily-quota", "setting-file-mib", "setting-retention-hours"]) ui[id].value = "";
    ui["user-defaults"].textContent = "正在读取系统默认值…"; ui["audit-note"].textContent = "管理审计保留规则独立于文件有效期";
    for (const id of ["setting-quota-help", "setting-file-help", "setting-retention-help"]) ui[id].textContent = "";
    ui["account-retention"].textContent = ""; ui["account-name"].textContent = ""; ui["account-role"].textContent = ""; ui["quota-summary"].textContent = ""; ui["quota-reset"].textContent = "";
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
    ui["auth-retention"].textContent = `新任务文件有效期：入队起 ${retention()}，排队和运行也占用有效期`;
    if (session) {
      ui["account-name"].textContent = session.user.username; ui["account-role"].textContent = session.user.is_admin ? "管理员" : "成员";
      ui["quota-summary"].textContent = usage ? `今日已用 ${usage.used} / ${usage.daily_quota} · 剩余 ${usage.remaining} 个文件 · ${usage.active_jobs || 0} 项处理中` : "正在读取额度…";
      ui["quota-reset"].textContent = usage ? `每日 UTC 00:00 重置 · 下次 ${utc(usage.resets_at)} · ${localTime(usage.resets_at)}` : "每日额度按 UTC 重置";
      ui["account-retention"].textContent = `新任务：入队起 ${retention()} · 已有任务以各自到期时间为准 · 账户单文件上限 ${bytes(usage?.max_file_bytes || session.user.max_file_bytes)}`;
    }
  }

  function renderQueue() {
    const fragment = document.createDocumentFragment();
    for (const entry of pending) {
      const row = element("li", "file-item"); row.dataset.state = entry.error ? "error" : "queued";
      const details = element("div", "file-select"); const text = element("div", "file-details");
      text.append(element("span", "file-name", entry.file.name), element("span", "file-meta", `${bytes(entry.file.size)} · ${entry.error ? "请检查" : uploadBusy ? "上传中" : "待上传"}`));
      if (entry.error) text.append(element("span", "file-error", entry.error));
      details.append(element("span", "file-icon", extension(entry.file.name).slice(1).toUpperCase().slice(0, 4) || "FILE"), text);
      row.append(details, actionButton("remove", entry.id, "×", `移除待上传文件 ${entry.file.name}`, uploadBusy || uploadUncertain)); fragment.append(row);
    }
    ui["file-list"].replaceChildren(fragment); ui["queue-empty"].hidden = pending.length > 0;
    ui["queue-count"].textContent = String(pending.length); ui["queue-size"].textContent = bytes(pending.reduce((sum, entry) => sum + (entry.error ? 0 : entry.file.size), 0));
    const unavailable = !session || !ready || uploadBusy || uploadUncertain;
    ui["choose-button"].disabled = unavailable; ui["file-input"].disabled = unavailable;
    ui["clear-button"].disabled = uploadBusy || !pending.length;
    ui["convert-button"].disabled = unavailable || uploadUncertain || !engineInfo().available || !pending.some((entry) => !entry.error) || usage?.remaining === 0;
    ui["convert-button"].classList.toggle("is-busy", uploadBusy); ui["drop-zone"].classList.toggle("is-busy", unavailable);
    ui["convert-label"].textContent = uploadBusy ? "正在上传…" : "上传并转换";
    ui["file-list"].setAttribute("aria-busy", String(uploadBusy));
    ui["drop-zone"].setAttribute("aria-disabled", String(unavailable));
    ui["upload-progress"].hidden = !uploadBusy; ui["upload-recheck"].hidden = !uploadUncertain;
    ui["conversion-summary"].textContent = uploadBusy ? "正在等待服务接收，完成后自动进入转换记录" : uploadUncertain ? "上传结果尚未确认。安全重试会沿用同一请求编号，不重复创建任务或计次" : usage?.remaining === 0 ? "今日额度已用完，请等待 UTC 重置" : `本次 ${pending.filter((entry) => !entry.error).length} 个可提交文件，最多使用同等次数额度；上传与重试入队均计次`;
    ui["upload-limits"].textContent = `最多 ${config.max_files} 个文件 · 单个 ${bytes(fileLimit())} · 总计 ${bytes(config.max_total_bytes)}`;
  }

  function renderEngines() {
    const selected = engineInfo();
    for (const id of ["markitdown", "docling"]) {
      const info = engineInfo(id), input = ui[`engine-${id}`];
      input.checked = selectedEngine === id; input.disabled = !session || !ready || uploadBusy || uploadUncertain || !info.available;
      ui[`engine-card-${id}`].classList.toggle("is-selected", selectedEngine === id);
      ui[`engine-card-${id}`].classList.toggle("is-unavailable", !info.available);
      ui[`engine-badge-${id}`].textContent = !info.available ? "未启用" : id === "markitdown" ? "默认" : "可用";
      ui[`engine-limit-${id}`].textContent = id === "docling" ? `仅文字型 PDF · 最多 ${info.max_pages} 页 / ${bytes(info.max_file_bytes)} · 最长 ${info.timeout_seconds} 秒 · OCR 关闭${info.available ? "" : `\n${info.reason}`}` : `无 2 页硬门槛 · 单文件 ${bytes(info.max_file_bytes)} · 最长 ${info.timeout_seconds} 秒 · OCR 关闭${info.available ? "" : `\n${info.reason}`}`;
    }
    ui["engine-group"].setAttribute("aria-busy", String(uploadBusy));
    ui["engine-selection-note"].textContent = !selected.available ? selected.reason : selectedEngine === "docling" ? `此批全部使用 Docling；整份超出 ${selected.max_pages} 页会拒绝处理，不会只转换前两页。OCR、代码/公式/图片/图表扩展关闭。实际大小仍取账号与引擎上限中更低的值。` : "此批全部使用 MarkItDown，无 2 页限制；长文档质量仍需核对。重试沿用原引擎，使用当前可用配置，不静默切换引擎。";
    const extensions = selectedEngine === "docling" ? config.extensions.filter((value) => value === ".pdf") : config.extensions;
    ui["file-input"].accept = extensions.join(",");
    ui["format-list"].replaceChildren(...extensions.map((value) => element("span", "", value.slice(1).toUpperCase())));
    ui["format-note"].textContent = selectedEngine === "docling" ? "短 PDF 实验 · 页数由服务入队前检查 · OCR 关闭 · 不支持 URL 上传" : "PDF 需要文字层，不支持扫描件 OCR · 不支持 URL 上传";
  }

  function renderHistory() {
    const fragment = document.createDocumentFragment();
    let visible = 0;
    for (const job of jobs) {
      const state = status(job), id = String(job.id), changing = jobMutations.has(id);
      if (state !== "succeeded") archiveIds.delete(id);
      if (historyFilter === "active" && !isActive(job) || historyFilter === "completed" && state !== "succeeded" || historyFilter === "failed" && !["failed", "expired"].includes(state)) continue;
      visible += 1;
      const row = element("li", "file-item"); row.dataset.state = state; row.classList.toggle("is-selected", selectedId === id);
      const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.checked = archiveIds.has(id); checkbox.dataset.action = "archive"; checkbox.dataset.id = id;
      checkbox.disabled = state !== "succeeded" || changing || archiveBusy; checkbox.setAttribute("aria-label", `将 ${job.filename} 加入 ZIP 下载`);
      const select = actionButton("select", id, "", `查看 ${job.filename}，${jobStatusName(job)}`); select.className = "file-select"; select.setAttribute("aria-pressed", String(selectedId === id));
      const details = element("span", "file-details"); const meta = element("span", "file-meta");
      meta.append(element("span", "", bytes(job.size_bytes || 0)), element("span", "engine-chip", engineInfo(job.engine || "markitdown").label), element("span", "file-state", jobStatusName(job)));
      details.append(element("span", "file-name", job.filename), meta, element("span", "file-meta", `入队 ${utc(job.created_at)} · 尝试 ${job.attempts || 1} / ${attemptLimit()}`), element("span", "file-meta", state === "expired" ? "文件已到期，不再提供下载" : `${utc(job.expires_at)} 到期`));
      if (job.error && state === "failed") details.append(element("span", "file-error", job.error));
      select.append(element("span", "file-icon", extension(job.filename).slice(1).toUpperCase().slice(0, 4) || "FILE"), details);
      row.append(checkbox, select);
      const actions = element("div", "history-actions");
      if (isActive(job)) actions.append(actionButton("cancel", id, changing ? "正在取消…" : "取消任务", `取消 ${job.filename}，已用额度不退还`, changing));
      if (state === "failed") actions.append(actionButton("retry", id, job.attempts >= attemptLimit() ? "重试次数已用完" : "重试", `重试 ${job.filename}，计入当日额度`, changing || job.lifecycle_status === "stopping" || job.attempts >= attemptLimit() || usage?.remaining === 0));
      actions.append(actionButton("delete", id, changing ? "处理中…" : "删除", `删除 ${job.filename} 及其文件`, changing)); row.append(actions); fragment.append(row);
    }
    ui["history-list"].replaceChildren(fragment); ui["history-empty"].hidden = visible > 0;
    ui["history-empty"].textContent = !jobs.length ? jobsBusy ? "正在读取转换记录…" : "还没有转换记录，添加第一个文件吧" : "此分类暂时没有记录";
    ui["history-count"].textContent = String(jobs.length); ui["refresh-button"].disabled = jobsBusy;
    ui["refresh-button"].textContent = jobsBusy ? "刷新中…" : "刷新";
    ui["history-list"].setAttribute("aria-busy", String(jobsBusy));
    const active = jobs.filter(isActive).length, done = jobs.filter((job) => status(job) === "succeeded").length, failed = jobs.filter((job) => ["failed", "expired"].includes(status(job))).length;
    ui["workspace-status"].textContent = jobs.length ? `${active} 项处理中 · ${done} 项已完成${failed ? ` · ${failed} 项需处理` : ""}` : "添加文件，开始整理";
    for (const [id, label, count] of [["all", "全部", jobs.length], ["active", "处理中", active], ["completed", "已完成", done], ["failed", "需处理", failed]]) { ui[`filter-${id}`].textContent = `${label} ${count}`; ui[`filter-${id}`].setAttribute("aria-pressed", String(historyFilter === id)); }
    ui["archive-button"].disabled = !archiveIds.size || archiveBusy; ui["archive-button"].textContent = archiveBusy ? "正在打包…" : "下载 ZIP";
    ui["archive-count"].textContent = archiveIds.size ? `已选择 ${archiveIds.size} / 10 个文件` : "勾选已完成文件，最多 10 个";
  }

  function renderDocument() {
    const job = selectedJob(), state = job ? status(job) : null;
    const done = state === "succeeded" && selectedDocument && String(selectedDocument.id) === selectedId;
    const hasText = done && selectedDocument.markdown.length > 0;
    const changing = jobMutations.has(selectedId), active = job && isActive(job), stopping = job?.lifecycle_status === "stopping";
    ui["copy-button"].disabled = !done; ui["download-button"].disabled = !done;
    ui["document-heading"].hidden = !job; ui["document-name"].textContent = job?.filename || "";
    ui["document-length"].textContent = done ? `${selectedDocument.markdown.length.toLocaleString("zh-CN")} 字符` : "";
    const metadata = selectedDocument?.metadata || job?.metadata || {};
    ui["document-engine"].textContent = job ? `${engineInfo(job.engine || "markitdown").label}${Number.isSafeInteger(metadata.page_count) && metadata.page_count > 0 ? ` · ${metadata.page_count} 页` : ""}` : "";
    ui["document-expiry"].textContent = job ? `${utc(job.expires_at)} 到期 · ${localTime(job.expires_at)}` : "请及时下载需要保留的内容";
    ui["output-label"].textContent = detailLoading ? "正在读取" : job ? jobStatusName(job) : "等待选择";
    ui["output-label"].dataset.state = state || "empty";
    ui["output-empty"].hidden = !!hasText; ui["preview-panel"].hidden = !hasText || view === "source"; ui["source-panel"].hidden = !hasText || view === "preview";
    ui["output-content"].classList.toggle("is-split", view === "split" && !!hasText);
    ui["output-content"].setAttribute("aria-busy", String(detailLoading || !!active || stopping));
    ui["preview-panel"].setAttribute("aria-labelledby", view === "split" ? "split-tab" : "preview-tab");
    ui["source-panel"].setAttribute("aria-labelledby", view === "split" ? "split-tab" : "source-tab");
    ui["document-progress"].hidden = !detailLoading && !active && !stopping;
    ui["preview-safety"].hidden = !done; ui["result-review"].hidden = !done; renderJobFacts(job, selectedDocument);
    ui["document-reload"].hidden = !detailError || state !== "succeeded";
    ui["document-retry"].hidden = state !== "failed";
    ui["document-retry"].disabled = changing || job?.lifecycle_status === "stopping" || job?.attempts >= attemptLimit() || usage?.remaining === 0;
    ui["document-retry"].textContent = job?.lifecycle_status === "stopping" ? "正在停止，资源释放后可重试" : job?.attempts >= attemptLimit() ? "重试次数已用完" : usage?.remaining === 0 ? "今日额度已用完" : "重试转换 · 使用一次额度";
    ui["document-cancel"].hidden = !active; ui["document-cancel"].disabled = changing;
    ui["document-cancel"].textContent = changing ? "正在取消…" : "取消此任务";
    for (const mode of ["preview", "source", "split"]) { ui[`${mode}-tab`].setAttribute("aria-selected", String(view === mode)); ui[`${mode}-tab`].tabIndex = view === mode ? 0 : -1; }
    if (done && renderedDocument !== selectedDocument) {
      // This is the only HTML insertion: the authenticated API sanitizes preview HTML.
      ui["markdown-preview"].innerHTML = selectedDocument.html;
      ui["markdown-source"].value = selectedDocument.markdown; ui["markdown-source"].scrollTop = 0; ui["preview-panel"].scrollTop = 0; renderedDocument = selectedDocument;
    } else if (!done) { ui["markdown-preview"].replaceChildren(); ui["markdown-source"].value = ""; renderedDocument = null; }
    let title = "下一站，Markdown", description = "上传文件，或选择一条转换记录\n在这里预览结果，或查看 Markdown 源码";
    if (detailLoading) { title = "正在读取结果…"; description = "正在加载你的转换内容"; }
    else if (stopping) { title = "正在停止任务"; description = "已阻止发布结果，执行进程退出后才释放资源\n此时不能重试；已用额度不退还，到期时间不延长"; }
    else if (state === "expired") { title = "文件已过期"; description = "原文件与结果已超过入队时确定的有效期，历史记录不代表文件仍可下载\n如需再次转换，请重新上传文件"; }
    else if (state === "failed") { title = job.error === "任务已取消" ? "任务已取消" : "这个文件暂时无法转换"; description = job.error === "任务已取消" ? "文件与记录仍保留，已用额度不退还\n停止中的任务需稍等片刻再重试" : job.error || "可在转换记录中重试"; }
    else if (active) { title = state === "queued" ? "已加入转换队列" : "正在整理文字…"; description = state === "queued" ? "正在等待可用的转换资源，状态会自动更新\n已接收的任务可在离开页面后继续处理" : "转换进度暂不可精确计算，状态会自动更新\n你可以继续添加文件，或稍后回来查看"; }
    else if (detailError) { title = "结果暂时无法读取"; description = `${detailError}\n选择这条记录可重新读取`; }
    else if (done && !hasText) { title = "没有可预览的文字"; description = "转换已完成，但结果为空\n请确认文档包含可提取的文字"; }
    ui["empty-title"].textContent = title; ui["empty-description"].textContent = description;
  }

  function render() {
    const active = document.activeElement, focusedAction = active?.dataset.action, focusedId = active?.dataset.id;
    renderAuth(); renderEngines(); renderQueue(); renderHistory(); renderDocument();
    if (focusedAction && focusedId) {
      const replacement = [...ui["history-list"].querySelectorAll("[data-action]"), ...ui["file-list"].querySelectorAll("[data-action]")].find((node) => node.dataset.action === focusedAction && node.dataset.id === focusedId);
      if (replacement && !replacement.disabled) replacement.focus({ preventScroll: true });
      else if (!replacement && session) {
        const fallback = focusedAction === "remove" ? ui["choose-button"] : ui["history-list"].querySelector('[data-action="select"]') || ui["refresh-button"];
        if (!fallback.disabled) fallback.focus({ preventScroll: true });
      }
    }
  }

  function scheduleTimers() {
    clearTimeout(pollTimer); clearTimeout(expiryTimer); pollTimer = expiryTimer = null;
    if (!session) return;
    if (jobs.some((job) => ["queued", "running"].includes(status(job)) || job.lifecycle_status === "stopping")) pollTimer = setTimeout(() => refreshJobs(), 2000);
    const next = jobs.map((job) => job.expires_at * 1000 - Date.now()).filter((delay) => delay > 0);
    if (next.length) expiryTimer = setTimeout(() => { if (!session) return; if (selectedJob() && status(selectedJob()) === "expired") clearDocument(); render(); scheduleTimers(); }, Math.min(...next, 2147483000) + 20);
  }

  function clearDocument() { detailSequence += 1; detailController?.abort(); detailController = null; selectedDocument = null; detailLoading = false; detailError = ""; }
  async function selectJob(id) {
    clearDocument(); selectedId = String(id); const job = selectedJob(); ui["copy-button"].textContent = "复制"; render();
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
      jobs = data.jobs; adoptUsage(data.usage); validatePending();
      for (const id of archiveIds) if (!jobs.some((job) => String(job.id) === id && status(job) === "succeeded")) archiveIds.delete(id);
      const selected = selectedJob();
      if (!selected) { selectedId = null; clearDocument(); }
      else if (status(selected) !== "succeeded") clearDocument();
      else if (!selectedDocument && !detailLoading && !detailError) void selectJob(selectedId);
      ui["history-note"].textContent = `仅展示当前账户最近 100 条记录 · 新任务入队起 ${retention()}；历史可能在文件到期后继续保留${config.history_seconds ? ` ${Math.round(config.history_seconds / 86400)} 天` : ""}`;
    } catch (error) { if (!stale(error) && epoch === currentEpoch && seq === jobsSequence) ui["history-note"].textContent = `暂未刷新：${message(error)}`; }
    finally { if (seq === jobsSequence && epoch === currentEpoch) { jobsBusy = false; render(); scheduleTimers(); } }
  }

  function validatePending() {
    let total = 0;
    for (const entry of pending) {
      const file = entry.file; entry.error = entry.serverError || "";
      if (!config.extensions.includes(extension(file.name))) entry.error = "暂不支持此文件格式";
      else if (selectedEngine === "docling" && extension(file.name) !== ".pdf") entry.error = "Docling 仅支持 PDF；可切换 MarkItDown";
      else if (!file.size) entry.error = "文件为空，请选择包含内容的文件";
      else if (file.size > fileLimit()) entry.error = `单文件不能超过 ${bytes(fileLimit())}`;
      else if (total + file.size > config.max_total_bytes) entry.error = `本批文件总计不能超过 ${bytes(config.max_total_bytes)}`;
      if (!entry.error) total += file.size;
    }
  }
  function addFiles(files) {
    if (!session || !ready || uploadBusy || uploadUncertain) return;
    const warnings = [];
    for (const file of files) {
      if (pending.length >= config.max_files) { warnings.push(`文件数量已达上限（${config.max_files} 个）`); break; }
      if (pending.some((entry) => entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified)) { warnings.push(`已跳过重复文件：${file.name}`); continue; }
      pending.push({ id: nextFileId++, file, error: "" });
    }
    resetUploadIntent(); validatePending(); ui["file-input"].value = ""; notice(warnings.join("\n")); announce(`待上传 ${pending.length} 个文件`); render();
  }
  async function upload(replay = false) {
    if (!session || uploadBusy || (!replay && uploadUncertain) || !ready || (!replay && !engineInfo().available)) return;
    validatePending();
    const entries = replay && uploadPayload ? uploadPayload.entries : pending.filter((entry) => !entry.error);
    if (!entries.length) { render(); return; }
    if (!replay && usage && entries.length > usage.remaining) { notice(`今日还可上传 ${usage.remaining} 个文件，请减少待上传文件。`); return; }
    try { if (!uploadKey) uploadKey = newIdempotencyKey(); } catch (error) { notice(message(error)); return; }
    if (!uploadPayload) uploadPayload = { entries: [...entries], engine: selectedEngine };
    const currentEpoch = epoch; uploadBusy = true; jobsSequence += 1; jobsBusy = false; clearTimeout(pollTimer); notice(""); render();
    const form = new FormData(); form.append("engine", uploadPayload.engine); for (const entry of entries) form.append("files", entry.file);
    try {
      const data = await request("/api/jobs", { method: "POST", body: form, idempotencyKey: uploadKey });
      if (!Array.isArray(data.jobs) || !Array.isArray(data.errors)) throw new Error("上传响应不完整。请安全重试同一批请求以取回接收结果。");
      jobsSequence += 1; jobsBusy = false;
      const ids = new Set(entries.map((entry) => entry.id));
      pending = pending.filter((entry) => {
        if (!ids.has(entry.id)) return true;
        const index = entries.findIndex((candidate) => candidate.id === entry.id);
        const rejected = data.errors.find((error) => Number.isInteger(error.file_index) ? error.file_index === index : error.filename === entry.file.name);
        if (!rejected) return false;
        entry.serverError = typeof rejected.error === "string" ? rejected.error : "文件未被服务接收"; entry.error = entry.serverError; return true;
      });
      resetUploadIntent();
      jobs = [...data.jobs, ...jobs.filter((job) => !data.jobs.some((added) => String(added.id) === String(job.id)))].slice(0, 100);
      if (data.jobs.length) { selectedId = String(data.jobs[0].id); historyFilter = "all"; clearDocument(); announce(`${data.jobs.length} 个文件已加入转换队列`); }
      if (data.errors.length) notice(data.errors.map((entry) => `${entry.filename}：${entry.error}（入队前拒绝，不计次）`).join("\n"));
      await refreshJobs();
    } catch (error) {
      if (!stale(error) && epoch === currentEpoch) {
        uploadUncertain = !error.status || error.status >= 500;
        if (!uploadUncertain) resetUploadIntent();
        notice(`${message(error)}${uploadUncertain ? "\n上传结果尚未确认。可刷新核对转换记录，或安全重试同一批上传；请求编号保持不变，不会重复计次。" : ""}`); void refreshJobs();
      }
    } finally { if (epoch === currentEpoch) { uploadBusy = false; render(); scheduleTimers(); } }
  }

  async function mutateJob(action, id) {
    if (!session || jobMutations.has(id) || !["retry", "cancel", "delete"].includes(action)) return;
    const job = jobs.find((item) => String(item.id) === id); if (!job) return;
    if (action === "retry" && (status(job) !== "failed" || job.lifecycle_status === "stopping" || job.attempts >= attemptLimit() || usage?.remaining === 0)) return;
    if (action === "cancel" && !isActive(job)) return;
    if (action === "delete" && !window.confirm(`删除「${job.filename}」？原文件、转换结果和记录将无法再通过工作台访问，无法恢复。已用额度不退还；这不等于安全擦除备份或日志。`)) return;
    let retryKey;
    if (action === "retry") {
      const intent = retryIntents.get(id);
      if (intent && intent.attempts === job.attempts) retryKey = intent.key;
      else { try { retryKey = newIdempotencyKey(); } catch (error) { notice(message(error)); return; } retryIntents.set(id, { key: retryKey, attempts: job.attempts }); }
    }
    const currentEpoch = epoch; jobMutations.add(id); jobsSequence += 1; jobsBusy = false; clearTimeout(pollTimer); render(); notice("");
    try {
      const data = await request(`/api/jobs/${encodeURIComponent(id)}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST", idempotencyKey: retryKey });
      jobsSequence += 1; jobsBusy = false;
      if (action === "delete") {
        jobs = jobs.filter((item) => String(item.id) !== id); archiveIds.delete(id);
        if (selectedId === id) { selectedId = null; clearDocument(); }
        announce("已删除文件与记录");
      } else {
        if (String(data.id) !== id || !Object.hasOwn(statusNames, data.status)) throw new Error("任务响应不完整，请刷新记录确认状态。");
        retryIntents.delete(id); jobs = jobs.map((item) => String(item.id) === id ? data : item); selectedId = id; clearDocument(); announce(action === "cancel" ? "已请求取消；已用额度不退还，资源可能等待进程退出才释放" : "已重新加入转换队列，使用当前配置并再计一次额度，原到期时间不变");
      }
      await refreshJobs();
    } catch (error) { if (!stale(error) && epoch === currentEpoch) { if (error.status && error.status < 500) retryIntents.delete(id); notice(message(error)); void refreshJobs(); } }
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
  async function copy(text, success, fallback) {
    const currentEpoch = epoch;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      if (epoch !== currentEpoch) return false;
      announce(success); return true;
    } catch {
      if (epoch !== currentEpoch) return false;
      const previous = document.activeElement, field = element("textarea", "clipboard-buffer");
      field.value = text; field.setAttribute("readonly", ""); field.setAttribute("aria-label", "临时复制内容"); document.body.append(field);
      let copied = false;
      try { field.focus(); field.select(); copied = document.execCommand?.("copy") === true; } catch { /* Manual selection remains available. */ }
      field.remove(); previous?.focus?.({ preventScroll: true });
      if (copied) { announce(success); return true; }
      if (fallback === "source") { view = "source"; renderDocument(); ui["markdown-source"].focus(); ui["markdown-source"].select(); }
      else if (fallback === "invite") { ui["new-invite-token"].focus(); ui["new-invite-token"].select(); }
      notice("浏览器未允许自动复制，已选中内容，请使用系统复制快捷键。"); return false;
    }
  }

  function setAuthMode(mode) { if (authBusy) return; authMode = mode; authMessage(""); renderAuth(); }
  function establishSession(data) {
    if (!data.user || typeof data.csrf_token !== "string" || !data.usage) throw new Error("登录响应不完整，请重试。");
    resetSession(); session = data; adoptUsage(data.usage); authMessage(""); render(); void refreshJobs();
  }
  async function authenticate(mode) {
    if (!ready || !config.has_admin || authBusy || session || logoutToken !== null) return;
    const username = ui[`${mode}-username`].value.trim(), password = ui[`${mode}-password`].value;
    if (!/^[a-z0-9_.-]{3,32}$/.test(username)) { authMessage("用户名需为 3–32 位小写字母、数字、下划线、点或短横线。", true); return; }
    if (Array.from(password).length < 6 || Array.from(password).length > 128 || new TextEncoder().encode(password).length > 512) { authMessage("密码长度需为 6–128 个字符，最多 512 UTF-8 字节。", true); return; }
    const token = ui["invite-token"].value.trim(); if (mode === "register" && !token) { authMessage("请填写管理员提供的邀请码。", true); return; }
    const currentEpoch = epoch; authBusy = true; authMessage(mode === "register" ? "正在创建账户…" : "正在登录…"); renderAuth();
    try {
      const data = await request(`/api/auth/${mode}`, { method: "POST", body: { username, password, ...(mode === "register" ? { invite_token: token } : {}) }, auth: false });
      ui[`${mode}-password`].value = "";
      if (mode === "register") { ui["invite-token"].value = ""; ui["register-username"].value = ""; ui["login-username"].value = username; authMode = "login"; authMessage("账户已创建。请用刚才设置的密码登录。"); renderAuth(); ui["login-password"].focus(); }
      else establishSession(data);
    } catch (error) { if (!stale(error) && epoch === currentEpoch) authMessage(mode === "register" ? inviteError(error) : message(error), true); }
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
      session = data; adoptUsage(data.usage); validatePending(); render();
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
      if (!engineInfo().available) selectedEngine = "markitdown";
      ready = true; ui["connection-notice"].hidden = true;
      if (!session && logoutToken === null && config.has_admin) {
        try { establishSession(await request("/api/me", { auth: false })); }
        catch (error) { if (!stale(error) && error.status !== 401) authMessage(message(error), true); else if (error.status === 401) authMessage("欢迎回来，登录后继续整理资料。"); }
      } else if (!config.has_admin) authMessage("请先由服务所有者完成管理员初始化。");
    } catch (error) { if (!stale(error) && epoch === currentEpoch) { ready = false; ui["connection-message"].textContent = message(error); ui["connection-notice"].hidden = false; authMessage("服务暂未连接，请重新连接后继续。", true); } }
    finally { if (seq === connectionSequence) { connecting = false; ui["reconnect-button"].disabled = false; validatePending(); render(); } }
  }

  function addFact(list, label, value) {
    list.append(element("dt", "", label), element("dd", "", value === null || value === undefined || value === "" ? "未记录" : String(value)));
  }
  function snapshotLabel(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return "未记录";
    const profile = typeof snapshot.profile === "object" ? JSON.stringify(snapshot.profile) : snapshot.profile;
    return `${snapshot.engine || "引擎未记录"} · ${profile || "profile 未记录"} · 配置版本 ${snapshot.config_version ?? "未记录"} · 引擎版本 ${snapshot.engine_version || "未记录"}`;
  }
  function renderJobFacts(job, detail) {
    ui["job-facts"].hidden = !job; ui["manifest-download"].disabled = !job;
    ui["job-facts-list"].replaceChildren(); ui["attempt-list"].replaceChildren();
    if (!job) return;
    const data = detail && String(detail.id) === String(job.id) ? detail : job;
    const list = ui["job-facts-list"];
    addFact(list, "任务 ID", job.id);
    if (status(job) === "expired") addFact(list, "可导出范围", "仅现存处理清单；原文件与转换结果已不可下载");
    addFact(list, "入队时间", `${utc(job.created_at)} · ${localTime(job.created_at)}`);
    addFact(list, "文件到期", `${utc(job.expires_at)} · ${localTime(job.expires_at)}`);
    addFact(list, "计次", data.quota_charged === true ? "本次已计入额度；取消、失败或删除不退还" : data.quota_charged === false ? "本次未计入额度" : "未记录，请以账户用量为准");
    addFact(list, "尝试与上限", `${data.attempts ?? "未记录"} / ${attemptLimit()}${data.attempt_history_complete === false ? "；旧任务历史不完整，不能补推过去的尝试" : `；已记录 ${data.accepted_attempts ?? data.attempt_history?.length ?? "未知"} 次接受记录`}；重试使用当前配置，到期时间不变`);
    addFact(list, "当前尝试 ID", data.current_attempt_id);
    addFact(list, "原文件 SHA-256", data.source_sha256);
    addFact(list, "首次提交配置", snapshotLabel(data.submission_snapshot));
    addFact(list, "来源记录", data.provenance_status === "recorded" ? "已记录来源与配置；这不是内容完整性评估" : "旧任务或服务未记录，不能补推来源与配置");
    if (data.error_code) addFact(list, "错误代码", data.error_code);
    if (data.lifecycle_status === "stopping") addFact(list, "资源状态", "正在停止；物理进程退出后才释放执行槽，暂不能重试");
    const metadata = data.metadata || {};
    if (metadata.page_count) addFact(list, "引擎报告页数", `${metadata.page_count}；未评估逐页文字覆盖`);
    if (metadata.quality_assessment) addFact(list, "内容质量", metadata.quality_assessment === "not_evaluated" ? "未评估，请核对原文" : metadata.quality_assessment);
    const reasons = { submission: "首次提交", retry: "用户重试", restart_recovery: "重启恢复", legacy: "旧任务" };
    for (const attempt of Array.isArray(data.attempt_history) ? data.attempt_history : []) {
      const row = element("li", "attempt-card");
      row.append(element("strong", "", `第 ${attempt.sequence ?? "?"} 次 · ${reasons[attempt.reason] || attempt.reason || "未记录"} · ${({ cancelled: "已取消", stopping: "正在停止", interrupted: "已中断", rejected: "已拒绝" })[attempt.state] || statusNames[attempt.state] || attempt.state || "未记录"}`));
      row.append(element("p", "", `${utc(attempt.accepted_at)} · ${attempt.quota_charged === true ? "已计次" : attempt.quota_charged === false ? "未计次" : "计次未知"}`));
      row.append(element("p", "", snapshotLabel(attempt.snapshot)));
      if (attempt.started_at) row.append(element("p", "", `开始 ${utc(attempt.started_at)}${attempt.finished_at ? ` · 结束 ${utc(attempt.finished_at)}` : ""}`));
      if (attempt.physical_released_at) row.append(element("p", "", `执行资源回收 ${utc(attempt.physical_released_at)}`));
      if (attempt.error) row.append(element("p", "file-error", attempt.error));
      ui["attempt-list"].append(row);
    }
  }
  function settingsMessage(text, error = false) { ui["settings-status"].textContent = text; ui["settings-status"].classList.toggle("is-error", error); }
  function settingsFieldsDisabled(disabled) {
    for (const id of ["setting-daily-quota", "setting-file-mib", "setting-retention-hours", "settings-save"]) ui[id].disabled = disabled;
    ui["settings-form"].setAttribute("aria-busy", String(settingsBusy));
  }
  function renderSettings(data, force = false) {
    if (!data || !Number.isSafeInteger(data.version) || !data.current || !data.bounds) throw new Error("服务设置响应不完整，请刷新。");
    for (const key of ["default_daily_quota", "default_max_file_bytes", "retention_seconds"]) {
      const bound = data.bounds[key];
      if (!Number.isFinite(data.current[key]) || data.current[key] <= 0 || !bound || !Number.isFinite(bound.min) || !Number.isFinite(bound.max) || bound.min > bound.max) throw new Error("服务设置数值或合法范围不完整，请刷新。");
    }
    if (settingsDirty && !force) { settingsMessage("保留了尚未保存的编辑；提交时会校验你开始编辑时的配置版本。"); return; }
    adminSettings = data; settingsDirty = false;
    const current = data.current, defaults = data.defaults || {};
    ui["settings-version"].textContent = `当前版本 ${data.version} · 最近修改人 ${data.updated_by ?? "未记录"} · ${data.updated_at ? utc(data.updated_at) : "修改时间未记录"} · 保存后对后续对应对象生效`;
    ui["setting-daily-quota"].value = String(current.default_daily_quota);
    ui["setting-file-mib"].value = String(current.default_max_file_bytes / 1048576);
    ui["setting-retention-hours"].value = String(current.retention_seconds / 3600);
    const specs = [["default_daily_quota", "setting-daily-quota", "setting-quota-help", 1, "次 / UTC 日", "新账户"], ["default_max_file_bytes", "setting-file-mib", "setting-file-help", 1048576, "MiB", "新账户；Docling 仍最多 10 MiB"], ["retention_seconds", "setting-retention-hours", "setting-retention-help", 3600, "小时", "新任务；已有到期时间不变"]];
    for (const [key, input, help, scale, unit, scope] of specs) {
      const bound = data.bounds[key];
      if (!bound || !Number.isFinite(bound.min) || !Number.isFinite(bound.max)) throw new Error("配置范围缺失，请联系管理员。");
      ui[input].min = String(bound.min / scale); ui[input].max = String(bound.max / scale);
      ui[help].textContent = `部署默认 ${defaults[key] === undefined ? "未记录" : defaults[key] / scale} ${unit} · 当前 ${current[key] / scale} ${unit} · 可设 ${bound.min / scale}–${bound.max / scale} ${unit} · 作用于${scope}`;
    }
    ui["user-defaults"].textContent = `新账户当前默认：${current.default_daily_quota} 次 / UTC 日、${bytes(current.default_max_file_bytes)}；已有账户以下列实际值为准。单账户可设 1–1000 次、1–20 MiB（部署上限仍生效）。`;
    settingsFieldsDisabled(settingsBusy);
  }
  function renderCapabilities(data) {
    const list = ui["service-config"]; list.replaceChildren();
    const deployment = data?.deployment || {};
    const labels = { max_file_bytes: "全局单文件上限", max_total_bytes: "每批总大小", max_files: "每批文件数", global_concurrency: "全局并发", per_user_concurrency: "每人并发", docling_concurrency: "Docling 并发", max_pending_jobs: "排队与运行总量", max_queue_jobs: "排队与运行总量", storage_budget_bytes: "逻辑存储预约预算", max_storage_bytes: "逻辑存储预约预算", max_markdown_bytes: "Markdown 输出上限", max_html_bytes: "HTML 输出上限", conversion_timeout: "普通转换超时", history_seconds: "到期后历史保留秒数", max_attempts: "每任务最多尝试", deployment_mode: "部署模式", application_version: "应用版本", secure_cookie: "安全 Cookie", cookie_secure: "安全 Cookie", session_seconds: "会话有效秒数", invite_seconds: "部署默认邀请秒数", cleanup_interval: "运行时清理间隔秒数", docling_enabled: "Docling 配置开关", password_min_length: "密码最少字符数" };
    addFact(list, "作用范围 / 修改方式", "整个服务；以下仅展示，变更需受控部署并重新验证");
    addFact(list, "应用版本", config.application_version || "未记录");
    addFact(list, "新任务有效期", `${duration(data?.effective?.retention_seconds ?? config.retention_seconds)}，从入队起计算`);
    for (const [key, value] of Object.entries(deployment)) {
      if (value === undefined || value === null) continue;
      addFact(list, labels[key] || key.replaceAll("_", " "), typeof value === "object" ? JSON.stringify(value) : key.endsWith("_bytes") ? bytes(value) : value);
    }
    for (const id of ["markitdown", "docling"]) {
      const info = engineInfo(id), engine = config.engines?.find((entry) => entry.id === id);
      addFact(list, `${info.label} 当前能力`, `${info.available ? "就绪" : "不可用"} · 单文件 ${bytes(info.max_file_bytes)} · ${id === "docling" ? `整份最多 ${info.max_pages} 页` : "无 2 页硬门槛"} · 最长 ${info.timeout_seconds} 秒 · OCR 关闭${engine?.version ? ` · ${engine.version}` : ""}${info.available ? "" : ` · ${info.reason}`}`);
    }
    addFact(list, "安全边界", "所有者隔离、身份校验、进程与网络保护不可在网页关闭；管理员不自动获得其他成员文件权限");
    addFact(list, "文件与历史", `到期后不能正常下载，清理在服务运行时执行；历史${config.history_seconds ? `最多再保留 ${Math.round(config.history_seconds / 86400)} 天` : "按部署策略保留"}。备份和审计是独立策略`);
  }
  async function saveSettings() {
    if (!session?.user.is_admin || !adminSettings || settingsBusy) return;
    const quota = Number(ui["setting-daily-quota"].value), fileBytes = Number(ui["setting-file-mib"].value) * 1048576, retentionSeconds = Number(ui["setting-retention-hours"].value) * 3600;
    const values = { default_daily_quota: quota, default_max_file_bytes: Math.round(fileBytes), retention_seconds: Math.round(retentionSeconds) };
    if (Math.abs(fileBytes - values.default_max_file_bytes) > 0.000001 || Math.abs(retentionSeconds - values.retention_seconds) > 0.000001) { settingsMessage("单文件上限须对应整数 bytes，有效期须对应整数秒。", true); return; }
    for (const [key, value] of Object.entries(values)) {
      const bound = adminSettings.bounds[key];
      if (!Number.isSafeInteger(value) || value < bound.min || value > bound.max) { settingsMessage("请按显示的合法范围填写设置；每日额度须为整数。", true); return; }
    }
    const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== adminSettings.current[key]));
    if (!Object.keys(changes).length) { settingsMessage("当前数值没有变化。"); return; }
    const current = adminSettings.current;
    const summary = [`保存服务配置版本 ${adminSettings.version} 的变更？`];
    if ("default_daily_quota" in changes) summary.push(`新账户默认每日额度：${current.default_daily_quota} → ${values.default_daily_quota} 次`);
    if ("default_max_file_bytes" in changes) summary.push(`新账户默认单文件上限：${bytes(current.default_max_file_bytes)} → ${bytes(values.default_max_file_bytes)}`);
    if ("retention_seconds" in changes) summary.push(`新任务入队后的文件有效期：${duration(current.retention_seconds)} → ${duration(values.retention_seconds)}`);
    summary.push("保存成功后用于之后的新账户或新任务；已有账户和任务到期时间不变。");
    if (!window.confirm(summary.join("\\n"))) return;
    const currentEpoch = epoch; settingsBusy = true; settingsFieldsDisabled(true); settingsMessage("正在校验版本并保存…"); adminSequence += 1; adminBusy = false; ui["admin-refresh"].disabled = true;
    try {
      const data = await request("/api/admin/settings", { method: "PATCH", body: { expected_version: adminSettings.version, changes } });
      settingsDirty = false; renderSettings(data, true); renderCapabilities(data);
      config.retention_seconds = data.effective?.retention_seconds ?? data.current.retention_seconds;
      config.default_daily_quota = data.current.default_daily_quota; config.default_max_file_bytes = data.current.default_max_file_bytes;
      renderAuth(); settingsMessage(`已保存配置版本 ${data.version}，仅对之后的新账户或新任务生效。`); void refreshAudit();
    } catch (error) {
      if (!stale(error) && currentEpoch === epoch) {
        if (error.status === 409) {
          try { const current = await request("/api/admin/settings"); renderSettings(current, true); renderCapabilities(current); settingsMessage("配置已被其他管理员修改。本次未保存，已载入最新值；请重新核对并编辑后再提交。", true); }
          catch (refreshError) { if (!stale(refreshError)) { adminSettings = null; settingsMessage(`配置冲突且刷新失败：${message(refreshError)}。请刷新管理信息后再编辑。`, true); } }
        } else settingsMessage(message(error), true);
      }
    } finally { if (currentEpoch === epoch) { settingsBusy = false; settingsFieldsDisabled(!adminSettings); ui["admin-refresh"].disabled = false; } }
  }
  function renderAudit(events) {
    const fragment = document.createDocumentFragment();
    const names = { "invite.create": "创建邀请", "invite.revoke": "撤销邀请", "user.update": "更新账户", "settings.update": "更新业务设置", "account.bootstrap": "初始化管理员", "account.register": "邀请注册", "account.recover": "受控账户恢复" };
    for (const event of events) {
      const row = element("li", "audit-card");
      row.append(element("strong", "", `${names[event.action] || event.action} · ${event.result || "结果未记录"}`));
      row.append(element("p", "", `${utc(event.created_at)} · 操作者 ${event.actor_id ?? "未记录"} · 对象 ${event.target_type || ""} ${event.target_id ?? "未记录"}`));
      row.append(element("p", "audit-values", `修改前 ${event.before === null || event.before === undefined ? "未记录" : JSON.stringify(event.before)} → 修改后 ${event.after === null || event.after === undefined ? "未记录" : JSON.stringify(event.after)}`));
      row.append(element("p", "field-help", `请求 ${event.request_id || "未记录"}${event.error_status ? ` · 状态 ${event.error_status}` : ""}`)); fragment.append(row);
    }
    if (!events.length) fragment.append(element("li", "field-help", "暂无管理审计记录"));
    ui["audit-list"].replaceChildren(fragment);
  }
  async function refreshAudit() {
    if (!session?.user.is_admin || auditBusy || ui["admin-panel"].hidden) return;
    const currentEpoch = epoch, seq = ++auditSequence; auditBusy = true; ui["audit-refresh"].disabled = true;
    try {
      const data = await request("/api/admin/audit");
      if (!Array.isArray(data.events)) throw new Error("审计响应不完整。");
      if (currentEpoch === epoch && seq === auditSequence) { renderAudit(data.events); ui["audit-note"].textContent = `当前返回 ${data.events.length} 条管理事件；仅显示最近记录。审计不包含正文、密码、会话或邀请码，保留规则独立于文件有效期。`; }
    } catch (error) { if (!stale(error) && currentEpoch === epoch) ui["audit-note"].textContent = `审计暂未读取：${message(error)}`; }
    finally { if (currentEpoch === epoch && seq === auditSequence) { auditBusy = false; ui["audit-refresh"].disabled = false; } }
  }

  function renderInvites(invites) {
    const fragment = document.createDocumentFragment();
    for (const invite of invites) {
      const row = element("li", "admin-card"), heading = element("div", "admin-card-heading");
      const used = Boolean(invite.used_at || invite.used_by || invite.is_used), revoked = Boolean(invite.revoked_at || invite.is_revoked), expired = invite.expires_at * 1000 <= Date.now();
      heading.append(element("span", "", `邀请码 #${invite.id ?? "—"}`), element("span", "", used ? "已使用" : revoked ? "已撤销" : expired ? "已过期" : "可使用"));
      row.append(heading, element("p", "", `${utc(invite.expires_at)} 到期 · ${localTime(invite.expires_at)}`));
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
      form.dataset.username = user.username; form.dataset.originalQuota = String(user.daily_quota); form.dataset.originalBytes = String(user.max_file_bytes); form.dataset.originalActive = String(Boolean(user.is_active));
      form.append(heading, element("p", "field-help", `对象 ${user.id} · 当前 ${user.daily_quota} 次 / UTC 日 · ${bytes(user.max_file_bytes)} · ${user.is_active ? "已启用" : "已停用"}`)); const fields = element("div", "user-fields");
      for (const [key, label, value, max] of [["daily_quota", "每日文件额度", user.daily_quota, 1000], ["max_file_mib", "单文件上限（MiB）", user.max_file_bytes / 1048576, 20]]) {
        const wrap = element("div"), id = `user-${user.id}-${key}`, labelNode = element("label", "", label); labelNode.htmlFor = id;
        const input = element("input"); input.id = id; input.name = key; input.type = "number"; input.min = "1"; input.max = String(max); input.step = key === "daily_quota" ? "1" : "any"; input.required = true; input.value = String(value); input.dataset.field = key;
        wrap.append(labelNode, input); fields.append(wrap);
      }
      const controls = element("div", "user-controls"), activeLabel = element("label"), active = element("input"); active.type = "checkbox"; active.checked = user.is_active; active.dataset.field = "is_active"; active.disabled = String(user.id) === String(session.user.id);
      if (active.disabled) active.setAttribute("aria-label", "当前账户不能停用自己");
      activeLabel.append(active, element("span", "", active.disabled ? "当前账户：不能停用自己" : "允许登录")); const save = element("button", "choose-button", "保存设置"); save.type = "submit"; save.setAttribute("aria-label", `保存 ${user.username} 的账户设置`);
      controls.append(activeLabel, save); form.append(fields, controls, element("p", "field-help", "范围：1–1000 次 / UTC 日；1–20 MiB。后续准入时生效，不退已用次数；Docling 与全局限制仍适用。")); row.append(form); fragment.append(row);
    }
    ui["users-list"].replaceChildren(fragment);
  }
  async function refreshAdmin() {
    if (!session?.user.is_admin || adminBusy || settingsBusy || ui["admin-panel"].hidden) return;
    const currentEpoch = epoch, seq = ++adminSequence; adminBusy = true; ui["admin-refresh"].disabled = true; adminMessage("正在读取管理信息…");
    try {
      const [invites, users, settings] = await Promise.all([request("/api/admin/invites"), request("/api/admin/users"), request("/api/admin/settings")]);
      if (seq !== adminSequence || currentEpoch !== epoch) return;
      if (!Array.isArray(invites.invites) || !Array.isArray(users.users)) throw new Error("管理信息不完整，请重试。");
      renderSettings(settings); renderCapabilities(settings); renderInvites(invites.invites); renderUsers(users.users); adminMessage(""); void refreshAudit();
    } catch (error) { if (!stale(error) && currentEpoch === epoch) adminMessage(message(error), true); }
    finally { if (currentEpoch === epoch && seq === adminSequence) { adminBusy = false; ui["admin-refresh"].disabled = false; } }
  }
  async function createInvite() {
    if (!session?.user.is_admin || inviteBusy) return;
    const ttl = Number(ui["invite-hours"].value || 24);
    if (!Number.isInteger(ttl) || ttl < 1 || ttl > 168) { adminMessage("邀请有效期须为 1–168 的整数小时。", true); return; }
    const currentEpoch = epoch; inviteBusy = true; ui["invite-create"].disabled = true; clearInvite();
    try {
      const data = await request("/api/admin/invites", { method: "POST", body: { ttl_hours: ttl } });
      if (ui["admin-panel"].hidden) return;
      if (typeof data.token !== "string") throw new Error("邀请码响应不完整，请刷新列表后重试。");
      ui["new-invite-token"].value = data.token; ui["invite-expiry"].textContent = `${utc(data.expires_at)} 到期 · ${localTime(data.expires_at)} · 一次性使用`; ui["invite-result"].hidden = false;
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
    if (id === String(session.user.id) && !isActive) { adminMessage("不能停用当前登录账户。", true); return; }
    const fileBytes = mib * 1048576;
    if (!Number.isSafeInteger(fileBytes)) { adminMessage("单文件上限必须对应整数 bytes。", true); return; }
    const originalActive = form.dataset.originalActive === "true";
    if (quota === Number(form.dataset.originalQuota) && fileBytes === Number(form.dataset.originalBytes) && isActive === originalActive) { adminMessage("账户设置没有变化。"); return; }
    if (!window.confirm(`保存账户 ${form.dataset.username}（${id}）的设置？\n每日额度 ${form.dataset.originalQuota} → ${quota} 次\n单文件 ${bytes(Number(form.dataset.originalBytes))} → ${bytes(fileBytes)}\n状态 ${originalActive ? "启用" : "停用"} → ${isActive ? "启用" : "停用"}\n后续准入生效，已用额度不退。${!isActive ? "停用将撤销会话并阻止发布结果；运行资源可能等待进程退出才释放。" : ""}`)) return;
    const currentEpoch = epoch; form.dataset.saving = "true"; const submit = form.querySelector("button"); submit.disabled = true;
    try {
      await request(`/api/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: { daily_quota: quota, max_file_bytes: fileBytes, is_active: isActive } });
      adminMessage("账户设置已保存。"); if (String(session.user.id) === id) { const data = await request("/api/me"); session = data; adoptUsage(data.usage); validatePending(); render(); }
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
  ui["clear-button"].addEventListener("click", () => { if (uploadBusy) return; if (uploadUncertain && !window.confirm("上传接收结果仍未知。清空将放弃本页的安全重试编号；已接收任务不会取消。请先核对历史，之后重新上传可能再次计次。继续清空？")) return; pending = []; resetUploadIntent(); ui["file-input"].value = ""; notice(""); render(); announce("已清空待上传文件"); });
  for (const id of ["markitdown", "docling"]) ui[`engine-${id}`].addEventListener("change", () => {
    if (!session || !ready || uploadBusy || uploadUncertain || !engineInfo(id).available) { renderEngines(); return; }
    selectedEngine = id; resetUploadIntent(); for (const entry of pending) entry.serverError = ""; validatePending(); notice(""); render();
  });
  ui["upload-recheck"].addEventListener("click", () => { if (uploadUncertain && uploadPayload) void upload(true); });
  for (const filter of ["all", "active", "completed", "failed"]) ui[`filter-${filter}`].addEventListener("click", () => { historyFilter = filter; renderHistory(); });
  ui["convert-button"].addEventListener("click", () => upload()); ui["refresh-button"].addEventListener("click", refreshJobs); ui["archive-button"].addEventListener("click", archive);
  ui["file-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (!button || button.disabled || uploadBusy || uploadUncertain) return; resetUploadIntent(); pending = pending.filter((entry) => String(entry.id) !== button.dataset.id); if (!pending.length) uploadUncertain = false; validatePending(); render(); });
  ui["history-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (!button || button.disabled) return; const { action, id } = button.dataset; if (action === "select") void selectJob(id); else void mutateJob(action, id); });
  ui["history-list"].addEventListener("change", (event) => { const input = event.target; if (input.dataset.action !== "archive" || input.disabled) return; if (input.checked && archiveIds.size >= 10) { input.checked = false; notice("ZIP 每次最多选择 10 个文件。"); } else if (input.checked) archiveIds.add(input.dataset.id); else archiveIds.delete(input.dataset.id); render(); });
  const views = ["preview", "source", "split"];
  for (const mode of views) {
    ui[`${mode}-tab`].addEventListener("click", () => { view = mode; renderDocument(); });
    ui[`${mode}-tab`].addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); view = event.key === "Home" ? views[0] : event.key === "End" ? views[views.length - 1] : views[(views.indexOf(mode) + (event.key === "ArrowRight" ? 1 : views.length - 1)) % views.length]; renderDocument(); ui[`${view}-tab`].focus(); });
  }
  ui["copy-button"].addEventListener("click", async () => {
    const job = selectedJob(), currentEpoch = epoch, id = selectedId;
    if (!selectedDocument || !job || status(job) !== "succeeded") return;
    const copied = await copy(selectedDocument.markdown, "Markdown 已复制", "source");
    if (copied && epoch === currentEpoch && selectedId === id) ui["copy-button"].textContent = "已复制 ✓";
  });
  ui["document-reload"].addEventListener("click", () => { if (selectedId && !detailLoading) void selectJob(selectedId); });
  ui["document-retry"].addEventListener("click", () => { if (selectedId) void mutateJob("retry", selectedId); });
  ui["document-cancel"].addEventListener("click", () => { if (selectedId) void mutateJob("cancel", selectedId); });
  ui["download-button"].addEventListener("click", () => { const job = selectedJob(); if (job && selectedDocument && status(job) === "succeeded") downloadLink(`/api/jobs/${encodeURIComponent(job.id)}/download`, ""); });
  ui["drop-zone"].addEventListener("dragenter", (event) => { event.preventDefault(); if (!session || uploadBusy) return; dragDepth += 1; ui["drop-zone"].classList.add("drag-over"); });
  ui["drop-zone"].addEventListener("dragover", (event) => event.preventDefault());
  ui["drop-zone"].addEventListener("dragleave", (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) ui["drop-zone"].classList.remove("drag-over"); });
  ui["drop-zone"].addEventListener("drop", (event) => { event.preventDefault(); dragDepth = 0; ui["drop-zone"].classList.remove("drag-over"); addFiles(Array.from(event.dataTransfer?.files || [])); });
  document.addEventListener("dragover", (event) => event.preventDefault()); document.addEventListener("drop", (event) => event.preventDefault());
  ui["admin-toggle"].addEventListener("click", () => { if (!session?.user.is_admin) return; ui["admin-panel"].hidden = !ui["admin-panel"].hidden; ui["admin-toggle"].setAttribute("aria-expanded", String(!ui["admin-panel"].hidden)); if (!ui["admin-panel"].hidden) void refreshAdmin(); else clearInvite(); });
  ui["settings-form"].addEventListener("submit", (event) => { event.preventDefault(); void saveSettings(); });
  for (const id of ["setting-daily-quota", "setting-file-mib", "setting-retention-hours"]) ui[id].addEventListener("input", () => { settingsDirty = true; });
  ui["audit-refresh"].addEventListener("click", refreshAudit);
  ui["manifest-download"].addEventListener("click", () => { const job = selectedJob(); if (job) downloadLink(`/api/jobs/${encodeURIComponent(job.id)}/manifest`, ""); });
  ui["admin-refresh"].addEventListener("click", refreshAdmin); ui["invite-create"].addEventListener("click", createInvite); ui["invite-dismiss"].addEventListener("click", clearInvite);
  ui["invite-copy"].addEventListener("click", () => { if (session?.user.is_admin && ui["new-invite-token"].value) void copy(ui["new-invite-token"].value, "邀请码已复制", "invite"); });
  ui["invites-list"].addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (button?.dataset.action === "revoke") void revokeInvite(button); });
  ui["users-list"].addEventListener("submit", (event) => { event.preventDefault(); if (event.target.tagName.toLowerCase() === "form") void saveUser(event.target); });
  window.addEventListener("focus", verifySession);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void verifySession(); });
  window.addEventListener("pagehide", () => { resetSession(); authMessage("返回页面后会重新验证登录状态。"); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) void connect(); });
  render(); void connect();
})();
