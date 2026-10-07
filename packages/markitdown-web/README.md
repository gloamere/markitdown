# MarkItDown 邀请制工作台 v1.0.0

dev 当前包含桌面产品重设计：`/` 为介绍与下载官网，`/app` 为服务工作区。
macOS / Windows 客户端见 [markitdown-desktop](../markitdown-desktop/README.md)。
这是后续预发布开发，不改既有 v1.0.0 标签或宣称新版已生产部署。
设计、真实下载与独立验收见 [桌面产品说明](../../docs/DESKTOP-PRODUCT.zh-CN.md)。

可共享的文档转 Markdown 工具：一次性邀请注册、登录、批量转换、任务历史、
取消与重试、预览/源码/对照、复制、MD/ZIP 下载与事实清单。管理员管理
账号、邀请、业务默认值和审计；管理员身份不允许读取他人的文档。

v1.0.0 的功能、生产隔离、真实 Docling、Chromium 自动化及 Mac 实际视觉
检查已在发布基线通过。最终版本提交通过打包、完整回归和精确提交 CI 后
才创建版本标签；最新结果见验收记录与草稿 PR。
详见 [v1 范围](../../V1_SCOPE.md) 和 [验收记录](../../docs/V1-VALIDATION.md)。
没有合并 main 或生产部署。版本变更见 [CHANGELOG.md](CHANGELOG.md)。

## 本机安装与启动

Python 3.12–3.14；推荐使用锁定依赖的 Python 3.12 环境：

```sh
python -m venv .venv
. .venv/bin/activate
export ORT_DISABLE_TELEMETRY=1
python -m pip install -r requirements-web.lock
markitdown-web bootstrap-admin --data-dir /absolute/private/data
markitdown-web serve --data-dir /absolute/private/data --port 8765
```

管理员本人在交互终端输入密码；没有默认账号或密码。所有入口统一允许
6–128 个字符，推荐长口令。初始化与服务必须使用同一目录。已有管理员
不能再次初始化；受控恢复见 [账号操作说明](../../docs/ACCOUNT-OPERATIONS.zh-CN.md)。
Windows 可激活 `.venv\Scripts\Activate.ps1`，依赖需在该平台重新验证。

CLI 始终只监听 `127.0.0.1`，关闭任意代理头信任。默认 `local` 模式用于
本机开发评估，不提供完整 OS 文件系统隔离。浏览器里的“服务器”是运行
程序的电脑；远程 VM 上的服务会收到上传文件，并非在浏览器电脑上转换。

## 两种明确的引擎

| 合同 | 普通 MarkItDown | 可选 Docling |
| --- | --- | --- |
| 输入 | PDF、DOCX、XLSX、TXT、MD、CSV、JSON | 仅 PDF |
| 文件上限 | 20 MiB，账号可更低；没有 2 页硬门槛 | 整份 1–2 页且 10 MiB，账号可更低 |
| OCR | 关闭 | 关闭；图像扫描页可能无文字 |
| 尝试总时限 | 45 秒，包含独立预览阶段 | 60 秒，包含独立预览阶段 |
| 并发 | 全局 2、每人 1 | 全局 1，仍占全局/个人槽 |
| 输出 | Markdown、安全预览、事实清单 | 同左，额外记录真实引擎版本/页数等 |

Docling 默认关闭，Linux 才可启用。固定 CPU 环境和模型安装见
[scripts/docling](../../scripts/docling/README.md)。超过两页拒绝整份 PDF，
不会静默截断或换引擎；普通 PDF 不继承两页限制。不提供 OCR、外部 URL、
云 AI、插件、原生 DoclingDocument JSON、图片保存或来源坐标编辑。

Markdown 是需要复核的机器输出。关键数字、单位、表头、脚注与阅读顺序
仍应对照原文。成功和空 warnings 不意味着内容完整或准确；清单不编造
质量分、未知版本或页坐标。复制/下载后的 Markdown 仍是不可信内容，
目标应用须使用自己的安全渲染配置。当前 ZIP 只有 MD 文件，不是带原件和
图片资产的完整档案。[格式和目的地合同](../../docs/MARKDOWN-CONTRACT.md)

## 任务、额度与配置

- 每批最多 10 文件 / 50 MiB，MD 输出 2 MiB、预览 4 MiB
- 每人默认每天 50 次，UTC 00:00 重置；界面显示本地时间
- 接收入队按文件计次；格式/大小/Docling 页数等前置拒绝不计次
- 入队后失败/取消/删除不退次；主动重试及恢复中断的运行任务再次计次
- 同账号同 Idempotency-Key 和相同文件请求重放返回同一任务，不重复扣次
- 同键改内容拒绝；删除后重放旧键不能重建任务；retry 也有独立幂等键
- 每次接收保存源 SHA256、业务配置版本与执行 profile；每个 attempt 保留
  原因、时间、计次、实际报告版本和产物摘要，旧记录缺版本显示“未记录”
- 取消先撤销发布权；界面区分“正在停止”，确认子进程组回收后才释放槽
- 重试采用当前批准配置并留下新快照，不改原提交快照或原 expires_at

SQLite 单实例队列与文件锁禁止多个调度器共享数据目录。不要增加多个
Uvicorn worker 或多个副本冒充横向扩容。逻辑存储预算默认 1 GiB，按原件
加每任务 6 MiB 输出预约，不是磁盘硬配额；上传、临时区、DB、备份另算。

管理员可调整新账号默认额度/文件上限和新任务保留期，有界且原子版本
检查，冲突需刷新。旧账号/任务不会被默认值回写。部署并发、模型、路径、
网络与隔离只读，不能通过网页关闭保护。账号停用撤销会话并阻止任务发布。

## 保留、维护与恢复

新任务从接收入队起默认保留 24 小时，可配置 1 小时–7 天，仅影响新任务。
自然到期即拒绝内容访问，服务运行时清理；历史元数据再保留最多 30 天。
显式删除清理文件与历史行，同时保留不含正文的删除记录防止旧备份复活。
取消保留源文件供重试，不延长到期时间。停机时不承诺定时物理删除。

维护清理过期数据、孤儿任务目录和超过 1 小时且不活跃的 engine-* 临时区；
成功/失败与计数可诊断。备份、审计和恢复日志有独立生命周期，不沿用文件
保留时间。下载副本和历史备份不会被普通删除自动擦除。

[备份恢复工具与合同](../../docs/BACKUP-RESTORE.md) 使用停止服务后的 SQLite
备份 API、文件哈希清单和最新独立恢复日志。恢复到新目录，过滤后来的
删除/停用、保留已用额度、撤销旧会话和邀请，并阻止旧密码复活。恢复与
备份目前限 POSIX 私有文件权限环境，Windows 需要另行实现 ACL 验收。

## 安全边界与受控部署

- Argon2id、登录限速、可撤销会话、HttpOnly/SameSite Cookie、CSRF 与同源校验
- 每个任务、预览、清单、下载、ZIP、重试和删除都检查当前账号所有权
- 文件路径/符号链接/压缩包条目与展开量/实体声明检查，不执行宏
- 转换后独立受限进程生成预览；服务端只验证固定无属性 HTML 语法，不信任
  解析器返回的 HTML；预览无脚本、原始 HTML、远程图片或可点击外链
- 本地模式只有子进程/资源和网络防护，不能当成完整生产沙箱
- production 模式强制 Linux 每任务 namespace/Bubblewrap + seccomp、专用
  只读运行时、单文件输入、私有临时区/输出、禁网和进程组回收；能力失败
  拒绝解析，无降级。详细资源口径、正向验收和镜像构建见
  [沙箱运行时合同](../../docs/SANDBOX-RUNTIME.md)
- 同源静态资源无 CDN/外部字体；文档、令牌及邀请码不存 localStorage
- ONNX 遥测在 Python 导入前关闭；不以 Python socket 补丁冒充 OS 隔离

[部署模板](../../deployment/README.md) 提供确切 HTTPS Origin/Secure Cookie、
受控反向代理、请求限制与服务管理示例。模板未在目标服务器执行；DNS、
证书、主机安全设置、真实账号和上线需要独立授权。正向隔离/资源/浏览器
门槛未完成时，不能把代码自测包装成生产可用。

## 开发检查

```sh
bash scripts/check-web.sh
.venv/bin/python scripts/evaluate_web.py --out .venv/synthetic-evaluation
```

包括 Ruff/Black/Mypy、API/身份/所有权/配置/审计/队列/恢复/隔离负向测试、
真实本地格式与上游离线回归、Node DOM/状态测试和合成关键值检查。
排除上游外部 arXiv 下载用例。真实 Docling 模型测试使用单独锁定运行时；
DOM 不替代真实浏览器、剪贴板/下载或移动视觉验证。合成样本不是用户反馈，
不据此报告总体准确率、容量或 P95。GitHub CI 与本地结果分开记录。

应用继承仓库 MIT 许可，保留上游声明；第三方依赖与模型遵循各自许可证。
开发在 dev，main 是独立发布边界；草稿 PR 不自动合并或部署。
