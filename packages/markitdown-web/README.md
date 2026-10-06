# MarkItDown · 邀请制资料工作台

面向小范围试用的中文文档转 Markdown MVP。独立包装上游 MarkItDown，不改变原库 API / CLI。当前版本提供多用户功能，但**尚未公开部署，也不是已通过生产安全验收的服务**。

## 已实现

- 管理员创建一次性邀请；邀请注册、登录、退出，会话可撤销
- 每人独立的文件、任务和历史；管理员也不能通过文档接口读取别人的内容
- 持久化 SQLite 任务队列、状态查询、取消、失败重试和中断恢复
- 文本型 PDF、DOCX、XLSX、TXT、MD、CSV、JSON 转 Markdown
- 中文暮色界面、引擎选择、历史筛选、预览 / 源码 / 双栏对照、复制、单文件下载、最多 10 个结果的 ZIP 下载
- 可选 Docling CPU 增强：最多 2 页 / 10 MiB 的文字型 PDF，优化多栏顺序与表格；默认关闭
- 最小管理界面：邀请创建/撤销、用户启停、每日额度和单文件大小限制
- 上传、任务和存储限制；原文件及转换结果从上传起默认保留 24 小时

暂不包含 OCR、AI 摘要、支付、邮件发送、密码找回、角色提升、公网部署或像素级版式复刻。扫描版 PDF 需要 OCR，本版本会提示无法提取文字。

## 本机开发启动

Python 3.12–3.14；当前测试环境是 Linux / Python 3.12。从仓库根目录运行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
export ORT_DISABLE_TELEMETRY=1
python -m pip install -r requirements-web.lock
markitdown-web bootstrap-admin
markitdown-web
```

`bootstrap-admin` 只在交互式终端询问用户名和密码，密码不回显。没有默认账号、默认密码或自动生成的真实管理员凭据；已有管理员时不能再次初始化。账号为 3–32 位英文、数字、下划线、点或连字符，统一转为小写；密码为 12–128 个字符，最多 512 UTF-8 字节。

打开 `http://127.0.0.1:8000`，登录后在“管理”中创建邀请。邀请码只在创建时显示一次，请自行通过合适渠道交给受邀者；没有接入邮件服务。受邀者使用用户名、密码和邀请码注册。

默认数据目录为启动目录下 `.markitdown-data`（已忽略，不应提交）。如指定其他目录，bootstrap 与启动必须使用同一个私有目录，优先放在仓库之外：

```bash
markitdown-web bootstrap-admin --data-dir /absolute/private/data
markitdown-web --data-dir /absolute/private/data --port 8765
```

也可使用 `MARKITDOWN_DATA_DIR`。目录、数据库、上传与结果文件使用限制性权限。运行账户仍能访问文件；这不是磁盘加密。

当前 CLI **只监听 127.0.0.1**，没有公网绑定选项。浏览器中的“服务器”是运行该程序的电脑：如果程序在远程 VM，上传会到该 VM；不会因此自动在浏览器所在电脑处理。

Windows 使用 `.venv\Scripts\Activate.ps1`，并用 `$env:ORT_DISABLE_TELEMETRY="1"` 设置环境变量。锁文件记录了 Linux / Python 3.12 的解析；其他平台如无兼容 wheel，可在隔离环境重新解析，然后重新测试：

```bash
python -m pip install -e 'packages/markitdown[pdf,docx,xlsx]' -e 'packages/markitdown-web[test]'
```

## 可选 PDF 增强

默认安装不包含 Docling。增强引擎采用独立 CPU 环境和本地模型，需要管理员显式启用；最多 **2 页、10 MiB、60 秒、单个增强转换并发**，OCR 关闭，不接受部分转换结果，也不自动切换引擎。详情、安装和安全边界见 [Docling 集成说明](../../docs/DOCLING-INTEGRATION.zh-CN.md)。

## 额度、队列与保留规则

| 项目 | 默认值 |
| --- | --- |
| 单文件 / 一批总大小 | 20 MiB / 50 MiB |
| 每批文件数 / ZIP 结果数 | 10 / 10 |
| 每人每日额度 | 50 次，UTC 00:00 重置；管理员可设 1–1000 |
| 每人单文件上限 | 管理员可设 1–20 MiB |
| 同时转换 | 全局 2 个，每人 1 个 |
| 等待 + 正在运行的任务 | 全局最多 100 个 |
| 逻辑存储预算 | 1 GiB，按源文件 + 每任务 6 MiB 输出预留；另需上传/工作进程临时空间 |
| 普通引擎的转换 + 预览 | 最长 45 秒，Markdown 2 MiB，HTML 4 MiB |
| Docling 增强 | 最多 2 页 / 10 MiB / 60 秒；全局 1 个，仍计入全局和每人并发 |
| 每任务最多尝试 | 3 次 |
| 原文件与结果 | 上传时起 24 小时 |
| 会话 | 绝对有效期 12 小时，每账户最多 20 个，超出淘汰最旧会话 |

有效文件被接受入队即使用额度；失败的转换也已占用资源，因此不退额度。用户主动重试，以及服务重启后恢复中断任务，各使用一次当日额度。格式不支持、空文件等入队前拒绝的文件不计入额度。删除任务不会退还已经使用的额度。

队列和状态保存在 SQLite。正常退出会等待正在执行的有限时工作结束；意外中断后，符合条件的任务恢复排队，源文件丢失、超过尝试次数或额度不足则保留失败状态。通过文件锁禁止两个调度器共用数据目录；**不要运行多个 Uvicorn worker 或多个服务副本**。

到期后接口立即拒绝预览/下载/重试。服务运行时每 60 秒清理文件；停机期间没有后台守护进程执行物理删除，重启会先清理。自然到期后，文件名、状态和时间等历史元数据最多再保留 30 天；显式“删除”会删除文件及历史行。下载到用户电脑的副本不受服务到期策略影响。“删除”不是安全擦除，也不清除另行制作的磁盘备份。

## 安全边界

- Argon2id 密码哈希；邀请码及会话只存哈希，邀请单次使用且支持过期/撤销
- HttpOnly、SameSite=Strict 会话 Cookie；每个已登录写操作校验 CSRF，另有同源 Origin 和自定义请求头检查
- 登录/注册在密码哈希前做持久化账户/IP 限速；不信任任意 X-Forwarded-For；认证和上传另有限并发入口
- 每个任务详情、取消、重试、删除、下载、ZIP 项目都按当前会话的用户 ID 检查；管理员没有文件越权通道
- 不用原始文件名构建磁盘路径；任务 ID、目录、符号链接、ZIP 条目和读取大小均校验
- Office 压缩包检查解压量、数量、路径、加密与 XML 实体声明，包含非 `.xml` 后缀的 XML 部件；不执行宏
- 每个文件的转换和预览在限时子进程执行，Unix 上限制 CPU、虚拟地址空间、输出、文件描述符并关闭 core dump；Linux 上父服务退出会杀死解析子进程
- 子进程不继承密钥/代理环境变量，禁用常规 Python 出站连接；导入前设置 `ORT_DISABLE_TELEMETRY=1`，不启用 AI、云转换、URL 转换或插件
- 预览禁用原始 HTML并净化白名单标签，不加载图片或激活文档链接；原始 Markdown 下载仍应视为不可信内容
- 同源静态资源，无 CDN/外部字体/遥测；前端不将文档、会话或邀请码放入 localStorage，退出会清空页面中的私有状态
- 成功响应带当前用户身份标记，供前端检测多标签页切换账号，避免不同账号内容混在旧界面中

ONNX Runtime 的导入后关闭 API 可能来不及阻止初始化事件，因此必须在初始化前使用环境变量。见 [官方隐私说明](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)。

Docling 额外在 Linux 子进程使用内核 seccomp 禁网与进程创建限制，但仍没有文件系统沙箱，具体限制见集成说明。

**子进程不是完整 OS 级安全沙箱**。解析器仍以服务用户权限运行；Python socket 防护不是内核网络隔离。Windows 缺少这里的 Unix 资源限制，Linux 的父进程退出保护也不适用于所有平台。不要以高权限运行，不要把此开发服务直接转发到公网。

## 开发检查

```bash
bash scripts/check-web.sh
```

包含 Ruff、Black、Mypy、API / 身份 / 所有权 / 队列 / 清理 / 真实格式 / 解析防护测试，以及相关上游 CSV/DOCX/XLSX/PDF 离线回归。排除上游 `test_markitdown_remote`，避免测试向 arXiv 发起下载；本产品不提供 URL 转换。

有 Node 18+ 时还检查 JS 语法和无外部依赖的 DOM/状态测试。模拟 DOM 不等于浏览器渲染、真实剪贴板权限或下载集成；云 VM 的真实浏览器验证曾被环境阻断，不能把这些模拟检查称作完整视觉验收。新增测试只使用隔离临时目录和合成账号/文档，不初始化真实生产账号。

更新依赖锁文件：`uv pip compile requirements-web.in -o requirements-web.lock`。

## 公开试用前仍需完成

参见 [部署验收清单](../../docs/MULTIUSER-RELEASE.zh-CN.md)。至少包括经过授权的部署环境与域名/HTTPS、Secure Cookie、受控反向代理、独立低权限解析容器/VM、内核级禁网及文件隔离、全局请求限速、容量/超时压测、备份与删除策略、账号恢复，以及真实浏览器验收。

本实现使用单实例 SQLite，不宣称支持横向扩容。`MARKITDOWN_COOKIE_SECURE=1` 可启用 Secure Cookie，但不是公开部署步骤，也不能替代 HTTPS。此次未配置公网访问、真实账号、工作流权限或分支保护。

[分支与上游同步策略](../../docs/BRANCHING.zh-CN.md)：开发在 dev，main 保留生产发布边界，草稿 PR 不自动合并。源代码继续遵循仓库根目录 MIT 许可，保留上游声明；依赖使用各自许可证。
