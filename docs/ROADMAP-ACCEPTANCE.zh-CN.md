# 文档路线实施与验收记录（2026-10-08）

应用验收基线：`98938961e9adf7234a11a610251f63db4faf5ee8`。
本记录区分工程实现、自动化证据和实际人工验收，不代表生产发布、签名或公证通过。

## 按交付文档顺序实施

| 顺序 | 已实施内容 | dev 提交 | 详细合同 |
| --- | --- | --- | --- |
| P0 版本绑定证据 | 校验真实下载字节；构建源、实际安装包载荷与打包运行时对应 | `54d68b8314a12bb49ebcea24f2bb05f552d91aab`；Windows CRLF 兼容修补 `66ec4f0951860d626584dfce72682d15733f6a43` | [版本绑定证据](RELEASE-EVIDENCE.zh-CN.md) |
| P0 能力与生命周期 | 完整请求时限、迟到响应隔离、未确认提交身份保持、未知保留期不猜测 | `45a9c22affee093bdc9e8927b091a6b925b794d5` | [请求与生命周期](CAPABILITY-LIFECYCLE.zh-CN.md) |
| P0 固定样本导出 | 字节稳定的合成样本；真实鉴权 API、MD/ZIP/独立清单对应 | `e064dbf0b945cf63ccfd09186304503d7dda458a` | [导出样本](EXPORT-CORPUS.md) |
| P1 键盘与可访问性 | 待处理动作焦点恢复、被动状态去重、键盘全流程和 200% 渲染缩放门禁 | `98938961e9adf7234a11a610251f63db4faf5ee8` | [键盘验收](ACCESSIBILITY-EVIDENCE.md) |

第一阶段首次 CI 的 Windows 失败来自仓库中既有 CRLF blob 与检出转换的差异。
修补只增加精确原始 blob 匹配，保留干净检出、索引、全部受跟踪文件及 CI 身份校验；
后续阶段均在该修补之上。没有用跳过校验换取绿色结果。

## 精确应用提交的 CI

截至 2026-10-08 02:42 UTC，以上应用基线三个工作流均为成功终态：

- [tests 37718629213](https://github.com/gloamere/markitdown/actions/runs/37718629213)：24 个作业全部成功，含真实沙箱 Chromium 和独立生产隔离门禁
- [desktop-build 37718629125](https://github.com/gloamere/markitdown/actions/runs/37718629125)：Mac 与 Windows 作业成功
- [pre-commit 37718629173](https://github.com/gloamere/markitdown/actions/runs/37718629173)：成功

此前各阶段的通过记录分别为：发布证据修补 tests 37714207029 / desktop 37714206998 / pre-commit 37714207145；生命周期 tests 37714935782 / desktop 37714935810 / pre-commit 37714935825；固定样本 tests 37717369977 / desktop 37717369947 / pre-commit 37717369976。

本地综合检查通过 719 项 Web 测试、99 个 DOM 状态场景、10 项浏览器脚本自检、
29 项桌面检查、202 项离线格式回归及 8 项工具测试。4 项本地 opt-in 生产测试明确跳过，
独立生产 CI 是另外的实际运行证据，不将跳过改写为通过。

固定样本直接运行 11/11 符合预期：8 项成功转换、1 项空文档和 2 项损坏文档按规定拒绝。
API 运行 14 个任务，核对 11 份成功输出及 3 个预期失败；不是“所有输入都成功转换”。

新增浏览器键盘场景在精确提交的沙箱 Chromium 中通过，完整浏览器流程为 13 项。
Mac 源码运行及 assembled app 均通过实际 Electron `setZoomFactor(2)` 检查：
连接页 CSS 宽 530/380，工作区 CSS 宽 720/480；Windows 连接页同样通过。
日志包含实际缩放比例、视口、无横向溢出及已启用主要控件的 Tab 可达性。
工作区 ZIP 按钮在该原生缩放探针中未选文件而禁用，未冒称该按钮已被 Tab 触达；
ZIP 的真实键盘选择和导出由浏览器键盘场景独立覆盖。

## 验收边界

构建 CI 记录的是实际检出的干净 PR 合并提交及其树，不把 dev 工作流 head 冒充载荷源提交。
CI 安装包及 sidecar 是有保留期限的测试产物，没有因此成为官网可下载的发布版本。
Windows 连接页、打包与载荷验证通过，不替代 Windows 安装、保存、取消及覆盖的人工验收。

生成的样本、原始截图、下载文件和原始验收包不公开提交。代码与可重现脚本在仓库内。
人工原生对话框、键盘缩放快捷键、像素复核、下游编辑器和屏幕阅读器的实际结果单独记录。

## 精确应用基线的 Mac 实测

2026-10-08 对 `98938961e9adf7234a11a610251f63db4faf5ee8` 的独立 Mac 验收回传：

- 实际 packaged app 的 Cancel/Retry/Delete 待处理焦点恢复通过，包括最后一行删除后回到筛选按钮，以及较新导航不被迟到响应夺取焦点
- 新登录后用真实 Tab 到文件按钮，按钮启用，单次 Enter 产生实际 chooser 事件；这是一次成功观察，不能证明不存在间歇性失败，也没有验收原生文件选择对话框的确认流程
- 通过已有辅助功能访问实际 macOS Save 面板，分别完成保存与取消；DownloadItem 状态为 completed/cancelled，保存字节与结果匹配
- 使用已安装的 Obsidian 1.13.7 和隔离离线 vault，打开导出、键盘编辑、保存、重开通过；原始导出哈希不变。未安装消费软件，未改动用户原 vault
- 实际 Electron 2× 渲染缩放下主要控件可达，使用 compositor `capturePage` 像素复核；13 项 Chromium、29 项桌面检查及固定样本 11/11/API ZIP/清单同时通过

本次发现 Playwright `fullPage` 在 2× 缩放时可能裁剪绘制区域，不能单凭这类截图宣布视觉通过。
可见原生窗口的 compositor 像素正常，并单独保留可靠图片。这是截图路径的证据限制，
没有据此改变应用布局或放宽自动化边界。

原始验收摘要和约 7.7 MB 证据包保存在 Mac 的忽略证据目录，未公开上传。
测试使用隔离实例；原服务和用户 Obsidian 数据未改变，测试进程已退出。
本次未执行 VoiceOver、OS 键盘缩放快捷键、原生文件选择确认、签名分发或公证。
真实保存/取消通过不代表所有安装、覆盖和辅助技术路径均已验收。

保留证据的 SHA-256（仅登记校验值，不提供公开下载）：

- `mac-acceptance-summary.json`：`beae21a1b99e398439566bdc826a7cecd0ed657f30bcebfaf841ee0e4fdc8cb6`
- `mac-9893896-evidence.zip`：`9528908c58c7bacd2feacf691b957386d5b957d62e7f91eb55d10be2e889fff4`

## 浏览器门禁后续记录

文档整理提交 `61838219735e4f3850d3bf6db2aee0492e855499` 的浏览器运行在前 12 项通过后，
等待键盘文件选择事件超时。未从日志确定是应用焦点还是拦截准备问题；没有将它计为通过。
排查发现固定 Playwright 版本首次事件订阅的拦截准备异步且不等待确认，存在可疑准备窗口。

仅测试脚本修补 `1de8a18f2373525f3fb1e1f3f08d2c73e1008e70` 在真实 Tab 遍历之前注册
选择器事件等待，保留单次 Enter，无指针/程序化聚焦/等待延时/重试绕过；加入无内容的事件计数诊断。
应用文件和原生测试文件没有变化。12 项脚本自检和 99 项 DOM 检查通过。

该修补的精确 CI 已全部成功：[tests 37721801605](https://github.com/gloamere/markitdown/actions/runs/37721801605)、
[desktop-build 37721801573](https://github.com/gloamere/markitdown/actions/runs/37721801573)、
[pre-commit 37721801574](https://github.com/gloamere/markitdown/actions/runs/37721801574)。
新键盘场景确实通过；这次成功不把原失败原因升级为已证实，也不保证不会再出现间歇性问题。
