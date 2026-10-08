# Docling 可选 PDF 增强：v1.0.0 集成说明

默认引擎仍为 MarkItDown。Docling 适合需要改善多栏阅读顺序、表格结构的短文字型 PDF；由用户明确选择，失败不会自动切换引擎，也不接受部分结果。当前没有 OCR、图片描述、AI 摘要、付费 API 或在线转换服务。

## 首版硬限制

| 项目 | 普通转换 | PDF 增强 |
|---|---|---|
| 格式 | PDF、DOCX、XLSX、TXT、MD、CSV、JSON | 仅 PDF |
| 单文件 | 最多 20 MiB，受账号更小限额约束 | 最多 10 MiB，受账号更小限额约束 |
| 页数 | 沿用原转换器 | **1–2 页** |
| 转换及预览墙钟时间 | 45 秒 | **60 秒** |
| 模型流水线协作限时 | 不适用 | 30 秒；部分完成即失败 |
| 转换进程 CPU 时间 | 35 秒 | 90 秒 |
| 虚拟地址空间 RLIMIT_AS | 1.5 GiB | 8 GiB |
| 常驻内存 RSS | 没有独立轮询上限 | 3 GiB，约 50 ms 轮询 |
| 临时文件逻辑总大小 | 原有资源限制 | 512 MiB，约 50 ms 轮询 |
| Markdown / HTML | 2 MiB / 4 MiB | 2 MiB / 4 MiB |
| 转换并发 | 全局 2、每用户 1 | 还受 Docling 全局 1 限制 |
| OCR | 不支持 | 显式关闭 |

2 页是保守准入上限，不保证所有复杂 2 页 PDF 都能转换成功。曾经的 20 页模型试验出现阶段超时，因此本次没有开放 5 页或 20 页。当前环境重建后的测试证据单独记录，不能把此前机器上的测量当作本轮复测。

RSS、磁盘阈值是父进程采样停止线，不是内核硬配额；RLIMIT_AS 是**虚拟地址空间**，不是 RSS。每次只启动一个新的 Docling 转换进程，不复用长期模型进程；CPU 亲和性限 2 个可用逻辑 CPU、Torch 线程设为 2，不能据此声称整个进程只有 2 个线程。

上传前的 PDF 页数检查也在独立受限进程：最多 8 秒、5 秒 CPU、1 GiB 虚拟地址空间、512 MiB RSS 停止线；全服务最多一个页数检查，繁忙时要求重试。它不占转换队列槽，也不扣额度，所以可以与一个实际 Docling 转换并行。运行环境检查另有串行锁和 30 秒缓存。

## 准入、持久任务和取消

- 上传字段 `engine` 仅接受 `markitdown` / `docling`，省略为默认引擎
- `GET /api/config` 返回引擎是否可用及公开限制；环境路径和异常堆栈不返回客户端
- 环境版本、依赖存在性、模型大小/哈希和 PDF 页数在扣额度、持久入队前检查；不在主服务导入 Torch、Docling、PDFium 或 OpenCV
- 可用性检查是元数据/依赖存在性/模型校验，并非对每次请求执行完整模型热身；二进制依赖异常等仍可能在运行时失败。安装脚本会另行做真实导入检查
- SQLite 增量迁移为旧任务设置 `markitdown`，保留已有账号、历史及额度；每个任务保存引擎和少量白名单结果元数据
- 重试、重启恢复和开始执行均重新检查引擎状态，不静默降级；已接收的任务即使后来环境故障也不会自动退回额度
- `POST /api/jobs/{id}/cancel` 对所有者开放，保留取消状态和源文件，可按既有尝试/配额规则重试；没有自动重传
- Docling 取消会终止并回收实际进程。普通转换保留原有有限时子进程，取消结果立即生效，实际资源槽直到进程退出后才释放
- 取消、删除、过期不会提前释放物理引擎/用户/全局槽。立即重试仍在退出的任务会返回 409
- 权限检查覆盖详情、取消、重试、下载及 ZIP；管理员不能绕过其他用户的文档所有权
- 原文件及结果默认从上传起 24 小时到期，沿用原清理规则

## 安装与启用

主服务照 [工作台 README](../packages/markitdown-web/README.md) 安装。Docling 保持单独 Linux x86_64 / Python 3.12.14 环境，约数 GiB 的额外磁盘开销；不要把模型放进 Git。

```bash
# 从仓库根目录执行。需要已安装的 Python 3.12.14 和 uv
scripts/docling/install.sh /absolute/path/docling-runtime

export ORT_DISABLE_TELEMETRY=1
export MARKITDOWN_DOCLING_ENABLED=1
export MARKITDOWN_DOCLING_PYTHON=/absolute/path/docling-runtime/.venv/bin/python
export MARKITDOWN_DOCLING_MODELS=/absolute/path/docling-runtime/models

# 仍使用主服务的 Python 环境，不用 Docling 的 Python 启动网页
.venv/bin/markitdown-web --data-dir /absolute/private/data
```

首次账号初始化必须由所有者在交互终端运行 `bootstrap-admin`；没有默认管理员或预设真实密码。关闭 `MARKITDOWN_DOCLING_ENABLED` 后普通引擎照常可用。缺少运行环境、模型、内核限制能力，或者使用当前不支持的 macOS/Windows Docling 环境，增强选项会禁用。

安装脚本固定官方 PyPI 与 PyTorch CPU 索引，不安装完整 Docling 元包，不拉取 OCR/CUDA/VLM 模型。具体包锁、模型不可变 revision、SHA-256、匹配许可证及可重建指令见 [scripts/docling](../scripts/docling/README.md)。模型只由安装阶段下载，转换进程没有模型下载功能。

## 解析安全边界

Docling 和预览渲染在独立 Python `-I` 子进程中执行。环境采用白名单，不继承密钥、代理配置或原用户 HOME；离线/遥测开关在所有导入前设置。仅管理员固定的模型目录可用，五个模型文件按内置 SHA-256 校验。

Linux 每个子进程加载 seccomp，禁止 IPv4/IPv6/Unix socket、网络调用及 io_uring 路径，并用真实内核 socket 调用验证生效。禁止 fork/vfork/非线程 clone、创建新会话/进程组和 exec；允许模型需要的线程。限制安装失败即拒绝处理。父进程死亡触发 SIGKILL；超时/取消先终止进程组，必要时 SIGKILL，确认退出后才归还任务槽。

**local 模式**具备上述进程、网络及资源限制，但没有独立文件系统隔离：解析器仍可能读取服务用户有权读取的其他本地文件，不可把它当作不可信多用户生产隔离。production 模式对两个引擎强制使用 `linux-bwrap-v1`。已验证基线 `f57faf7e1ac5d5a46f5034dd15818f588aa72ce0` 的指定 Linux CI 全部 24 个任务通过，其中生产边界/生命周期 62 项、普通格式 7/7、Docling 7/7 阶段通过；Docling 覆盖五个固定模型文件、真实探测、两页预检/转换/独立预览、三页 `page_limit` 拒绝和清理。详见 [SANDBOX-RUNTIME.md](SANDBOX-RUNTIME.md) 和 [V1-VALIDATION.md](V1-VALIDATION.md)。未知目标主机仍需部署验收，当前没有公网部署。

普通 MarkItDown 的 local 模式保留开发用 Python 网络阻断；production 模式则与 Docling 一样要求文件系统命名空间、内核 seccomp 和真实监督回收，不会自动退回 local。

## 界面

暮色工作区提供引擎卡片、文件拖放/键盘选择、待上传与历史筛选、取消/重试、真实不定进度、预览/源码/对照视图及单文件/ZIP 下载。接口错误保留待上传文件；不确定的上传响应会要求先核对历史，避免重传重复扣额度。不会显示虚构完成百分比。

DOM/状态测试可检查交互和防陈旧响应逻辑，不能代替浏览器视觉、真实剪贴板、下载和移动设备验收。基线 `f57faf7` 的 Linux CI 真实 Chromium 自动化已通过全部 12 个场景；同一提交的原生 Mac Chrome 无痕窗口已完成桌面及 390 × 844 响应式视觉复核，登录、邀请、管理员设置/审计、键盘焦点、上传队列和预览/源码对照清晰可读，未观察到横向溢出。邀请双击只新增一次邀请及一次审计，TXT 转换结果正确且只扣一次额度。临时测试服务、数据库及无痕窗口已清理，原服务未受影响。390 × 844 是响应式模拟，不是实体移动设备测试；更广泛的功能证据来自 Linux CI。原云环境限制未被绕过，原始测试路径、截图及合成输入/输出不公开。

以上为功能和视觉验证基线，不代表尚未测试的后续提交已通过。带 `1.0.0` 版本号的最终提交仍须通过源码/安装包一致性、完整回归及精确 SHA 的 CI，之后才可建立不可变 `v1.0.0` 标签；最终标签目标及[发布 PR #1](https://github.com/gloamere/markitdown/pull/1) 中对应的 CI 结果为最终版本验证依据。此流程不包含合并 main 或部署。

## 检查

```bash
bash scripts/check-web.sh
.venv/bin/python -m pytest -q scripts/docling/tests/test_runtime_tools.py
# 真实模型/合成文档/API 检查的具体参数见该脚本 --help
ORT_DISABLE_TELEMETRY=1 .venv/bin/python scripts/docling/benchmark.py --help
```

全部新增测试使用临时目录及合成身份/文档；不使用真实用户文档、外部转换 API，也不替用户初始化持续管理员访问。
