# PDF 增强限制与界面升级验收记录

日期：2026-10-06。基于公开 dev 的 `255950046e6587497083fd1cae5b724b0c66a9c3`，本次记录对应发布前的本地开发检查点。以下结果来自云工作区重建后的**新实测**，不沿用之前机器上的计时。

## 结论

保留普通 MarkItDown 为默认引擎，Docling 作为手动选择的短 PDF 增强，固定最多 **2 页、10 MiB、60 秒、同时 1 个增强转换**。没有把一次小样本成功推广成所有两页文档必然成功，也没有开放 5 页。

新界面包含可用性明确的引擎卡片、拖放与键盘操作、历史筛选、取消/重试、可恢复错误状态及预览/源码/对照。真实浏览器视觉验收仍待进行，DOM 测试不替代截图或浏览器实测。

## 真实 API 与任务队列

17/17 检查通过，观察到 26 个真实子进程；实际使用认证 API、SQLite 调度器、独立 Docling CPU Python 与固定本地模型。所有输入和账号都是合成的；测试结束删除临时账号、数据库、任务和工作目录。测试期间应用源码 SHA-256 未变。

- 3 页 PDF 在扣额度、持久入队之前拒绝，耗尽额度与零队列容量时仍验证此边界
- 超过 10 MiB、损坏或加密 PDF 拒绝，配额和任务文件不增加
- 1 页文本、1 页多栏/表格、2 页多栏/表格成功；预览确实由受限子进程产生
- 无文字层扫描件明确失败，OCR 关闭，无 Markdown 成功输出、结果元数据或自动回退
- 不同用户的两个 Docling 转换不重叠，第三个用户的普通转换可同时进行
- 实际取消的子进程收到 SIGTERM，退出码 -15，约 **0.0247 秒**回收；资源槽一直保持到回收之后，下一个增强任务随后启动
- 取消后真实重试保留引擎；新建 JobService、复用 SQLite 后保留元数据并执行等待任务
- 子进程及临时文件清理通过

“重启”测试是使用保留状态的新服务对象，不是整机重启或掉电测试。完整证据和源码/样本/输出摘要见 [summary.json](evidence/docling-2026-10-06/summary.json)、[benchmark.json](evidence/docling-2026-10-06/benchmark.json)。

## 时间、内存、质量

时间是本轮单次 API 墙钟观测，含入队前校验；每个转换启动新进程，未清空操作系统缓存。RSS 每约 10 ms 采样，不是内核记录的精确进程峰值。子进程保留了生产路径中的网络/进程/资源防护。

| 合成输入 | Docling API 墙钟 | 采样峰值 RSS |
|---|---:|---:|
| 1 页文本 | 8.7374 秒 | 1115.11 MiB |
| 1 页多栏与表格 | 9.9688 秒 | 1144.14 MiB |
| 2 页多栏与表格 | 11.7155 秒 | 1237.06 MiB |

2 页样本：Docling 的两页阅读顺序均正确，表格 24/24 单元格按行列位置匹配。普通 MarkItDown 对相同样本也保留 24/24 表格单元格，但两页的栏目顺序均未达到预期。因此本次证据支持阅读顺序改善，**不能声称这组简单表格只有 Docling 才能正确转换**。

普通转换子进程约 0.6347 秒、采样 RSS 121.93 MiB；当时与 Docling 同时执行，且测量口径为子进程时间，不应把它与上表 API 墙钟当作严谨独立速度倍数比较。小组合成语料不是广泛准确率基准，也不验证中文 OCR、复杂长表、跨页表合并、损坏文件覆盖度或所有生产负载。

## 回归检查

最终检查以 `scripts/check-web.sh` 为入口：

- 325 项工作台、权限、队列、转换与资源契约测试
- 202 项上游离线格式回归，另 1 项外部 URL 测试排除
- 39 组前端 DOM/状态测试
- 8 项 Docling 安装/校验脚本测试
- Ruff、Black、Mypy、JavaScript/安装脚本语法和差异空白检查
- 工作台 wheel 成功构建，包含新引擎模块与静态资源

已有 Starlette/httpx TestClient 弃用警告未消除。模拟测试包含部分结果拒绝、进程超时、取消、RSS/临时空间阈值、符号链接、错误信息清洗、预览净化以及真实内核禁网/禁止 fork/保留线程能力；不能把模拟注入当成重新执行了长 PDF 模型超时实验。

## 可重建环境

- Python 3.12.14；Docling Slim 2.133.0；Torch 2.14.1+cpu / torchvision 0.29.1+cpu
- 共 75 个固定依赖分发包；独立 `uv.lock`，CPU 索引只用于 Torch 两项
- 五个模型文件共 384,428,156 字节，固定 Heron 和 TableFormer revision、SHA-256 与许可证
- 恢复后的独立环境约 1.75 GiB；安装守卫最多 8 GiB，并保留至少 10 GiB 磁盘空间
- 模型目录只读；转换过程中禁网、无隐式下载、OCR/VLM 未启用

安装和完整版本/模型许可见 [scripts/docling/README.md](../scripts/docling/README.md)。复测命令：

```bash
ORT_DISABLE_TELEMETRY=1 .venv/bin/python scripts/docling/benchmark.py \
  --python /absolute/docling-runtime/.venv/bin/python \
  --models /absolute/docling-runtime/models \
  --fixtures /absolute/docling-runtime/fixtures \
  --output /absolute/private/new-benchmark-output
```

记录生成时尚未提交或推送；后续源代码保存以 dev 的提交和草稿 PR 为准。本次没有合并 main 或部署服务。发布前仍需真实浏览器验收、专用低权限解析环境与文件系统隔离；详见 [集成说明](DOCLING-INTEGRATION.zh-CN.md)。
