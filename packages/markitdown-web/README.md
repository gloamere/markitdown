# MarkItDown · 资料工作台

在本机将文档转成 Markdown 的中文轻量工作台。独立包装上游 MarkItDown 转换器，不修改原库 API 或 CLI。

## 功能与边界

- 文本型 PDF、DOCX、XLSX，以及 TXT / Markdown / CSV / JSON
- 拖入或选择一批文件，逐文件反馈结果，查看渲染预览或 Markdown 源码
- 复制、单文件 `.md` 下载、移除、清空与失败重试
- 一批最多 10 个文件，每个 20 MiB，合计 50 MiB；单文件转换最长 45 秒，Markdown 输出最多 2 MiB
- PDF 需要文字层；暂不做扫描件 OCR、AI 摘要、图片提取、URL 抓取、ZIP 上传或复杂版式复刻
- 不接入付费服务、遥测、CDN 或在线字体；转换内容不会发送给 AI 或其他服务

“本机”指运行服务的电脑。浏览器上传会将文件发送到这台运行服务的电脑；如运行在远程 VM，文件也会到该 VM。当前命令仅监听 `127.0.0.1`，不是公网服务。

## 快速开始

Python 3.12–3.14；建议使用独立虚拟环境。从**仓库根目录**运行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.lock
markitdown-web
```

浏览器打开 `http://127.0.0.1:8000`。自定义端口：`markitdown-web --port 8765`。
Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活。提交的锁文件记录了本次 Linux / Python 3.12 的依赖解析；其他平台或 Python 版本如遇 wheel 不兼容，可在隔离虚拟环境中使用：

```bash
python -m pip install -e 'packages/markitdown[pdf,docx,xlsx]' -e 'packages/markitdown-web[test]'
```

这会重新解析依赖，需重新运行测试。无需 Node 构建前端。

## 安全与隐私

- 仅允许本机 Host；转换接口检查同源 Origin（如有）与自定义请求头，不开放跨域
- 请求体有 51 MiB 上限（含 multipart 开销），文件数量、单文件和批次大小另行校验
- 原始文件名只用于展示；去除路径、控制字符与危险字符，不用作磁盘路径
- Office 压缩包检查解压体积、条目数量、路径、加密与 XML 实体声明；不执行宏
- 每个文档在独立短期子进程中调用明确指定的上游转换器，不启用通用 URL 转换、插件或 AI
- 转换子进程不继承密钥/代理环境变量；禁用常规 Python 出站连接，Unix 上尽力限制 CPU、内存、文件大小和文件描述符
- 导入上游依赖前设置 `ORT_DISABLE_TELEMETRY=1`，禁用 ONNX Runtime 初始化遥测；测试脚本也在进程启动前设置。依据 [ONNX Runtime 官方隐私说明](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)，导入后才调用关闭 API 可能来不及阻止初始化事件
- 预览禁用原始 HTML，并进行标签白名单净化；预览不加载图片，不激活文档中的链接
- 源码与下载保留转换后的 Markdown 内容，可能包含链接或原始 HTML。在其他软件打开时，仍需将它视为不可信文档
- 正常完成或失败后关闭上传临时文件并删除转换目录；浏览器仅在当前页面内存保留结果，无服务器历史、无 localStorage
- 强制终止服务/主机故障可能留下操作系统临时文件；“删除”不等同于安全擦除。服务不记录上传内容或文件名，默认关闭访问日志

这些是本机 MVP 的纵深防护，**不是 OS 级沙箱，也不能保证解析器不存在漏洞**。Windows 不提供此处的 Unix 资源限制。不要将无认证接口转发到公网，也不要以高权限账户运行。恶意样本应在无敏感数据、无网络的专用容器/VM 中处理。

## API

```bash
curl http://127.0.0.1:8000/api/health
curl -H 'X-MarkItDown-Request: 1' \
  -F 'files=@example.docx' -F 'files=@table.xlsx' \
  http://127.0.0.1:8000/api/convert
```

`GET /api/config` 返回格式和大小限制。转换响应为 `{"results":[{"filename":"example.docx","markdown":"...","html":"...","error":null}]}`；每个文件单独反馈，损坏文件不会隐藏其他成功结果。整批请求不合法时返回 400 / 403 / 413。

## 开发检查

```bash
bash scripts/check-web.sh
```

覆盖 Ruff、Black、Mypy、Web API/安全/真实格式转换测试与相关上游 CSV/DOCX/XLSX/PDF 离线回归。明确排除上游 `test_markitdown_remote`（会向 arXiv 发起请求）；此 Web 产品不提供 URL 转换。有 Node 18+ 时另外检查 JavaScript 语法和 17 项模拟 DOM 状态测试（Node 24 验证通过）。模拟 DOM 不验证真实浏览器渲染、剪贴板权限或下载集成。完整上游多平台 CI、OCR/云服务和浏览器 UI 测试不等同于这组本地检查。

更新依赖锁文件（在 Python 3.12 的项目环境中）：

```bash
uv pip compile requirements-web.in -o requirements-web.lock
```

分支策略及首次同步 SHA 见 [`docs/BRANCHING.zh-CN.md`](../../docs/BRANCHING.zh-CN.md)。所有源代码继续遵循仓库根目录 MIT 许可；保留 Microsoft/上游版权及许可声明，依赖使用各自许可证。
