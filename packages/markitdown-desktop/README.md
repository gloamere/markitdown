# MarkItDown Desktop · 1.1.0-dev.1

macOS / Windows 的在线服务客户端。不是离线转换器，不打包 Python 或 Docling。
现有 v1.0.0 标签不变；本轮桌面设计作为 1.1 预发布开发。

## 开发与构建

```sh
cd packages/markitdown-desktop
npm ci
npm run check
npm start
# 在 macOS arm64 主机
CSC_IDENTITY_AUTO_DISCOVERY=false npm run build:mac
# 在 Windows x64 主机
set CSC_IDENTITY_AUTO_DISCOVERY=false
npm run build:win
```

锁定 Electron 44.6.0 / electron-builder 26.15.3，最低 macOS 12。
构建工具的 `global-agent` 固定 4.1.3，去除旧代理日志依赖的 DoS 通告；
安装后 npm audit 报告 0。该依赖仅在构建环境使用，不打入客户端业务代码。
Windows x64 目标为 NSIS 安装器；支持范围须经真实 Windows 验收。
本机 arm64 包不代表 Intel Mac / Windows 已测。打包不会自动发布。
默认生成包在 `dist/`，文件名带 `unsigned`；没有 Apple Developer 签名、公证、
Windows 代码签名或自动更新。系统可能阻止运行。请等待签名版本或在组织
批准的测试环境评估，不删除 quarantine、不关闭 Gatekeeper/SmartScreen。

## 连接与会话

首次启动填写管理员提供的服务根地址。远程只允许 HTTPS，HTTP 仅允许
127.0.0.1 / localhost / ::1。客户端验证 `/api/config`，在同源 `/app` 使用
现有认证、CSRF、HttpOnly Cookie、任务所有权、幂等与额度规则。
仅服务地址写入应用配置；登录 Cookie 使用不持久化的独立内存分区。
切换服务/关闭工作区清除该分区，服务已接收的任务继续执行。关闭客户端
不等于服务端注销；服务端会话按原有有效期到期或由退出登录撤销。

所有文档上传到连接的服务；可选 Docling 仍只在已启用的 Linux 服务运行。
客户端不弱化 Origin 校验、不加 CORS、不自启多个服务或调度器。
旧服务必须升级到含 `/app` 路由的版本。真实 8765 服务未被更改或重启。

## 桌面边界

远程工作区无 preload / Node API；所有渲染进程开启 sandbox、contextIsolation、
webSecurity。导航、重定向、网络请求限定服务 Origin，弹窗和 webview 禁止。
摄像头、麦克风、通知等权限拒绝，复制使用现有安全手动回退。
本地连接界面用受限自定义协议，IPC 验证窗口、主 frame 与确切地址。
下载仅接受当前服务的已认证结果地址或同源 Blob，使用原生保存对话框；
取消保存不修改任务，文件名不能选目录。不放行证书错误。

## 分发

已有本机 Developer ID 的独立签名测试、公证认证准备与实际 DMG 验收，见
[Mac 分发与测试说明](../../docs/MAC-DISTRIBUTION.zh-CN.md)。普通构建与 CI
仍是 unsigned；单独 `npm run build:mac:signed` 输出到 `dist/developer-id/`，
明确标记已签名、未公证，不自动改动官网下载。

`.github/workflows/desktop-build.yml` 在 dev 推送/PR 构建未签名测试产物，
Actions 成功且工件实际存在才可引用。工件需要 GitHub 登录并有保留期限，
不能作为永久官网安装地址。官网本地下载可由 `scripts/stage-desktop-downloads.py`
对真实产物生成大小、SHA256 与下载清单；没有清单就显示“尚未提供”。
正式官网分发需独立确认托管目标、持久下载地址、签名证书与公证凭据。
当前不自动创建 Release、不修改标签、不部署线上。

安全设计依据：[Electron 官方安全建议](https://www.electronjs.org/docs/latest/tutorial/security)。

## 独立验收

```sh
# 在具备正常窗口与回环能力的环境，临时服务/用户/数据自动清理
npm run test:smoke
# 仓库根目录，需安装 web 的开发依赖
ORT_DISABLE_TELEMETRY=1 .venv/bin/python scripts/verify-desktop-service.py --out /tmp/native-evidence
```

第一项验证合成 HTTP 服务的协议拒绝、取消、重复连接、Origin/权限和返回。
第二项在真实 API/转换工作进程上验证合成成员、真实 Markdown、额度、历史、
原生 DownloadItem 字节保存与取消、迟到结果与退出清空。原生保存路径在
测试中被确定化；这验证下载机制，不代表人工操作保存对话框的验收。
真实个人资料和原有服务不作为测试输入，窗口以后台显示，不使用 Chrome。
