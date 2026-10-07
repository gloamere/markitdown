"use strict";
const form = document.getElementById("connect-form"), input = document.getElementById("service-origin"), button = document.getElementById("connect-button"), cancel = document.getElementById("cancel-button"), status = document.getElementById("status");
let busy = false, sequence = 0;
function finish() { busy = false; button.disabled = input.disabled = false; cancel.hidden = true; button.textContent = "连接服务 →"; }
window.desktop.info().then((info) => { input.value = info.origin; document.getElementById("version").textContent = info.version; status.textContent = info.message; }).catch(() => { status.textContent = "无法读取客户端状态，请重新打开应用。"; });
window.desktop.onStatus((message) => { sequence += 1; finish(); status.textContent = message; });
form.addEventListener("submit", async (event) => {
  event.preventDefault(); if (busy) return;
  busy = true; const attempt = ++sequence; button.disabled = input.disabled = true; cancel.hidden = false; status.textContent = "正在检查服务与连接安全性…"; button.textContent = "连接中…";
  try { const result = await window.desktop.connect(input.value); if (attempt === sequence) status.textContent = result.ok ? "已连接，进入工作台。" : result.message; }
  catch { if (attempt === sequence) status.textContent = "连接中断，请重新连接。"; }
  finally { if (attempt === sequence) finish(); }
});
cancel.addEventListener("click", async () => { sequence += 1; cancel.disabled = true; try { await window.desktop.cancel(); status.textContent = "已取消连接。"; finish(); input.focus(); } finally { cancel.disabled = false; } });
