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
Windows x64 目标为 NSIS 安装器；支持范围须经真实 Windows 验收。
本机 arm64 包不代表 Intel Mac / Windows 已测。打包不会自动发布。
生成包在 `dist/`，文件名带 `unsigned`；没有 Apple Developer 签名、公证、
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

`.github/workflows/desktop-build.yml` 在 dev 推送/PR 构建未签名测试产物，
Actions 成功且工件实际存在才可引用。工件需要 GitHub 登录并有保留期限，
不能作为永久官网安装地址。官网本地下载可由 `scripts/stage-desktop-downloads.py`
对真实产物生成大小、SHA256 与下载清单；没有清单就显示“尚未提供”。
正式官网分发需独立确认托管目标、持久下载地址、签名证书与公证凭据。
当前不自动创建 Release、不修改标签、不部署线上。

安全设计依据：[Electron 官方安全建议](https://www.electronjs.org/docs/latest/tutorial/security)。
