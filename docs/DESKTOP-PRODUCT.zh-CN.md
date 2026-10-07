# MarkItDown 桌面产品与官网重设计

本轮在 dev 开发，桌面客户端版本 `1.1.0-dev.1`。既有 Web 包版本及
`v1.0.0` 标签保持不变；没有合并 main、发布新标签或部署生产服务。

## 产品结构

- `/`：介绍与下载官网。真实功能、工作流程、实际工作区截图、安装包状态和常见问题。
- `/app`：同源服务工作区。文档转换、任务与历史、阅读/源码/对照、导出与处理清单。
- 管理员侧栏：独立管理后台。成员、邀请码、业务默认值、只读服务能力和内容安全的审计。
- 桌面启动页：填写服务根地址、失败提示、重试、取消。菜单支持返回连接页和切换服务。

浅色布局使用绿色强调色、系统字体、固定侧栏、分层面板、完整键盘焦点
和窄屏导航。细则与保留规则可展开，关键限制及复核提示继续可见。
结果和待提交文件在工作区/历史切换时保留，退出或会话中断立即清空。
官网不使用虚构客户、用户数据、准确率、商业承诺或装饰性下载按钮。

## 技术与服务边界

Electron 44.6.0，electron-builder 26.15.3，锁定 npm 依赖。
构建工具 global-agent 4.1.3 覆盖修复其旧日志依赖通告，npm audit 为 0。
所有渲染进程 sandbox/contextIsolation/webSecurity 开启；远程页面无
Node/preload。自定义协议仅服务打包内的连接界面，IPC 校验窗口、主 frame
和精确地址。网络、导航、重定向限定选择的服务 Origin，弹窗、webview 和
无关权限拒绝，不放行证书错误。

远程地址必须 HTTPS，HTTP 仅允许明确回环地址。客户端检查 `/api/config`
与 `/app`，旧服务缺少桌面路由时提示升级，不把 404 当成功连接。
Cookie、CSRF、HttpOnly、Origin 检查、所有权、幂等、额度、取消与队列策略
沿用现有服务。客户端不加入 CORS 或另起调度器。服务地址单独记忆；
会话使用内存分区，切换服务清除本地状态。关闭客户端不等于服务端注销，
已接收任务继续由服务处理，重新登录后从历史确认结果。

客户端不内置转换引擎，不离线转换。文件上传到所连接的服务；Docling
仅在已启用的 Linux 服务执行，仍为整份 1–2 页 / 10 MiB、关闭 OCR 的
可选能力，不宣称 Mac 或 Windows 原生 Docling。生产隔离要求没有更改。
官网仅根页面允许同源截图；工作区仍为 `img-src 'none'`，安全预览不开放图片。

## 安装包和真实下载

2026-10-08 的后续 Mac 签名核实、独立 Developer ID 构建及真实 DMG 测试
入口见 [Mac 分发与测试说明](MAC-DISTRIBUTION.zh-CN.md)。下述 unsigned
状态描述本轮官网下载及默认构建，单独签名入口不会自动覆盖它们。

macOS Apple Silicon：DMG 与 ZIP，最低 macOS 12。Windows x64：NSIS EXE。
本轮均为未签名测试构建，Mac 未公证，没有自动更新。系统可能阻止运行；
不删除 quarantine、不关闭 Gatekeeper/SmartScreen，不绕过系统警告。
签名/公证需要组织证书与独立授权，目前未操作任何证书或凭据。

构建工作流不会创建 GitHub Release 或改动标签。Actions 工件保留 14 天，
通常需要 GitHub 登录，不能充当永久官网下载地址。

本地官网从 `downloads.local.json` 读取实际加入的构建。仅当固定目录中
存在匹配文件、大小与 SHA256 合法时显示下载链接；没有产物显示“尚未提供”。
清单和安装包被 gitignore 与 Web wheel 排除，普通代码推送不伪装成分发。

```sh
# 仓库根目录；必须传入实际构建，参数可按已有平台选择
.venv/bin/python scripts/stage-desktop-downloads.py \
  --mac packages/markitdown-desktop/dist/MarkItDown-1.1.0-dev.1-mac-arm64-unsigned.dmg
# 已从成功 Windows CI 获得 EXE 时，可同时加 --windows /absolute/path/to/EXE
```

正式官网上线与永久下载托管尚未执行；需要确认目标与签名分发安排。
当前真实 8765 服务未重启，使用它连接新客户端前需由运营者升级到包含
`/app` 的服务版本。所有验收使用新的临时数据库与随机回环端口。

## 本地验证与视觉证据

2026-10-07，本轮工作区本地通过：

- `bash scripts/check-web.sh`：Ruff/Black/Mypy，657 Web 测试通过、8 条平台/运行时条件跳过；7/7 合成格式检查，9 个浏览器 runner 自检，8 个 Docling 工具测试，202 上游离线格式测试，60 DOM/状态场景。
- 真实沙箱 Chromium runner：12 组 API/上传/权限/管理/下载/冲突/退出中断/返回/390 与 320 窄屏流程通过。这里不运行真实用户研究或生产 Docling 模型。
- 真实 Electron 连接流程：HTTPS 拒绝、旧服务升级提示、取消、重复提交、沙箱/无远程桥、导航拒绝、返回连接页。
- 真实 Electron + 独立服务：合成成员登录、真实 Markdown 转换、重复上传仅扣 1 次、历史/源码/对照、DownloadItem 保存字节和取消、真实迟到结果被退出阻止、返回。原生保存目的地在测试中被确定化，未宣称人工对话框或安装体验验收。
- 官网真实 Chromium：1440 / 390 / 320 宽度、无水平溢出、实际截图正常加载、FAQ/锚点交互，Mac DMG 和 Windows EXE 两项实际下载的大小与 SHA256 均通过。
- 本机 Mac arm64 DMG / ZIP 实际生成。Windows x64 EXE 已从成功的 Actions 构建下载到本机。开发版原生界面测试不等于从下载包安装的系统验收；完整 Windows 业务/安装体验没有人工验收。

本地截图与原始 JSON 保留在被忽略的 `docs/evidence/desktop-ui/`，只含
合成输入。Codex 已对实际像素检查桌面、登录/退出、管理、历史/对照和官网
窄屏的可读性、间距与遮挡；这不替代用户或无障碍专家验收。官网使用的
[工作区截图](../packages/markitdown-web/src/markitdown_web/static/workspace-preview.png)
来自实际 Electron 与真实服务，不是 HTML 假界面。

两项已有跨平台测试问题修正了测试工具而非安全边界：macOS 在生产路径
检查前拒绝 Linux 沙箱，因此其固定安全错误码应被断言；格式评估器对自己
创建的临时目录 resolve，避开 macOS `/var` 别名，转换器仍拒绝符号链接输入。

第一节 CI 的生产进程生命周期测试曾遇到 procfs 退出竞态：进程在读取
`/proc/<pid>/stat` 时消失，Linux 返回 ESRCH。观察器现在只将 ENOENT 与
ESRCH 视为进程消失，权限错误继续失败；三个回归用例验证这条边界。
安全执行器与生产权限没有更改。第二节完整 24 项 CI 和双端构建已通过。

双端实际构建来自代码 `49ef02d0444a3f9a992f58cad261a36c4cd872eb`：
[Mac/Windows 构建及 smoke](https://github.com/gloamere/markitdown/actions/runs/37639261480)，
[完整测试](https://github.com/gloamere/markitdown/actions/runs/37639271678)。
最终测试观察器修复不改变这两个安装包的运行时代码。

## 查看成果

交付时独立预览使用随机回环端口和临时空数据库，具体存活地址以最终交付
为准。官网与安装包下载可直接查看；`/app` 显示真实连接/登录界面，因为
该预览没有初始化账户。不会为演示填写真实管理员密码。完整任务和管理
页面的合成验收截图分别位于 `docs/evidence/desktop-ui/native-committed/`
和 `docs/evidence/desktop-ui/browser-final/screenshots/`，官网最终截图位于
`docs/evidence/desktop-ui/site-final/`。

Mac 可直接查看本机实际构建 `packages/markitdown-desktop/dist/mac-arm64/MarkItDown.app`；
或从预览官网下载未签名 DMG。Windows EXE 同样通过官网实际下载。
这些路径不表示系统安装签名验收已完成。预览停止后，操作者可用新的私有
目录和空闲端口运行 `.venv/bin/python -m markitdown_web serve --port PORT --data-dir DIRECTORY`。

## 复验

```sh
bash scripts/check-web.sh
cd packages/markitdown-desktop
npm ci
npm run check
npm run test:smoke
cd ../..
ORT_DISABLE_TELEMETRY=1 .venv/bin/python scripts/verify-desktop-service.py --out /tmp/native-evidence
# 需 requirements-browser-test.in 及官方 Chromium，保持浏览器沙箱开启
.venv/bin/python packages/markitdown-web/tests/browser_e2e.py --out /tmp/browser-evidence
.venv/bin/python scripts/verify-product-site.py --out /tmp/site-evidence
```

新增桌面 CI 验证 Mac/Windows 构建和连接流程，Mac 另验证真实服务业务流程；
浏览器 CI 另验证官网。确切远端 SHA 与最新 CI 结果以最终交付中的链接为准。
没有通过或未执行的检查不能借用旧 v1.0.0 结果。

## 后续小幅优化

竞品启发、已核实功能与分阶段开发状态见
[桌面后续设计与调整](DESKTOP-REFINEMENTS.zh-CN.md)。
