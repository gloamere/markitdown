# Mac 签名、公证与实际安装包测试

默认 Mac/Windows 构建仍输出明确标记的 unsigned 测试包。Developer ID
签名使用单独入口和 `dist/developer-id/` 目录，不自动发布、不覆盖官网
原有 unsigned 下载，也不把签名等同于公证或 Gatekeeper 分发验收。

## 核实与缺少时的准备

本机只读检查使用 `security find-identity -v -p codesigning`、公开证书期限、
Xcode 缓存团队类型以及 `xcrun --find notarytool` / `stapler`。不导出私钥、
读取账户令牌、改变钥匙串访问策略或代替操作者输入 Apple 凭据。

已有可用 Developer ID Application 时优先复用；Apple Development 是开发
身份，不能代替独立官网分发所需的 Developer ID。DMG 中的 App 使用
Developer ID Application；只有另做 PKG 安装器时才需要考虑 Developer ID
Installer。[Apple 证书说明](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)

Xcode 的非免费团队缓存不能证明当前会员有效期。需要操作者在
[Apple Developer 账户](https://developer.apple.com/account/)确认对应团队的
会员状态，或用已授权的 notarytool 配置验证在线公证服务访问。
如果会员不存在，再选择个人或组织报名：个人需要有效的 Apple Account、
双重认证与身份核验；组织还需法律实体、D-U-N-S 和签约权限。
会员年费为 99 USD，地区价格以 Apple 结算为准；报名、协议和付款由
操作者确认。[Apple 入会说明](https://developer.apple.com/help/account/membership/program-enrollment/)

如果证书确实缺少，由具备权限的 Account Holder 在 Certificates,
Identifiers & Profiles 创建 Developer ID Application。先使用钥匙串生成
CSR，私钥留在创建它的本机；提交 CSR、下载 CER 并安装到同一钥匙串。
本项目不会为“准备”而撤销现有证书、生成重复私钥或把 P12/密码写入仓库。

## 本机签名构建

```sh
cd packages/markitdown-desktop
# 是公开的现有证书名称，不是密码。替换下列占位名称。
CSC_NAME='Developer ID Application: YOUR NAME (YOURTEAMID)' npm run build:mac:signed
```

入口确认身份存在后强制 Developer ID 签名，开启 hardened runtime 和严格
验证，使用最小的 `allow-jit` 权限；不增加摄像头、麦克风、蓝牙、
get-task-allow、未签名执行内存或关闭 library validation 的权限。
App、助手和框架由签名工具处理，签名使用安全时间戳；构建完成再进行
deep/strict 验证和预期签名者检查。任一必要检查失败时构建失败。

产物名称为 `developer-id-unnotarized`，客户端连接页显示已签名/未公证。
当前入口显式禁用自动公证，避免意外使用环境内的 Apple 凭据。
默认 unsigned CI 不读取或上传本机证书，签名流程不自动修改标签或 Release。

## 公证认证准备

如果已有配置，只需要把 `notarytool` 的 profile name 提供给工程流程；
不要发送 Apple 密码、应用专用密码、API 私钥或验证码。
如果配置缺少，操作者在自己的交互式终端执行：

```sh
xcrun notarytool store-credentials "markitdown-notary"
```

该命令会交互式询问账户、团队和认证信息，默认向 Apple 验证后写入钥匙串。
使用与签名团队一致的 Apple Account。不要使用 `--password` 参数或把
凭据放入聊天、shell 历史、仓库或普通文档。此步骤尚未由本项目代为执行。

获得授权的配置后，先只读验证服务访问，再提交签名 DMG，等待 Accepted，
对产物 staple/validate，重新检查 Gatekeeper 和下载包。未收到 Accepted
不能宣称已公证；staple 改变字节后必须重新生成大小与 SHA256。
[Apple Developer ID 分发说明](https://developer.apple.com/developer-id/)

## 实际 DMG 验收

```sh
# 仓库根目录，需已有 browser-test 依赖；DMG 必须是真实签名产物。
ORT_DISABLE_TELEMETRY=1 .venv-ui/bin/python scripts/verify-mac-package.py \
  --dmg packages/markitdown-desktop/dist/developer-id/MarkItDown-1.1.0-dev.1-mac-arm64-developer-id-unnotarized.dmg \
  --out docs/evidence/desktop-ui/mac-signed-package
```

测试只读挂载 DMG，不唤起 Finder，把 App 复制到新的临时目录，保留属性。
检查 Developer ID、时间戳、hardened runtime、最小权限和 Gatekeeper；
如果下载/隔离属性存在且 Gatekeeper 拒绝，阻止原生启动，不清除属性或
绕过系统警告。没有隔离属性的本机构建可做开发功能测试，Gatekeeper 的
分发拒绝仍单独记录，不会被功能通过覆盖。

功能验证使用实际打包执行文件及 Electron 原生 `--user-data-dir` 隔离目录，
自动核对 `app.isPackaged` 和真实数据路径。只以后台窗口显示；业务会话
仍为内存分区，远程页面没有 Node/preload，沙箱与 Origin 限制继续开启。
独立临时数据库与随机端口验证取消、重复连接、导航边界、真实成员登录、
转换、额度、历史、导出取消、退出时迟到响应与返回。原生保存目的地由
测试确定化；没有宣称用户手动保存、拖入 Applications 或全新 Mac 验收。
最后正常卸载本次独有的卷并清理临时目录，截图与 JSON 保留在忽略目录。

Mac CI 另外验证普通构建中组装后的真实 App，防止仅源码模式测试通过。
所有流程避免原有 `.markitdown-data` 与 8765，不填写真实管理员密码。

## 2026-10-08 本机结果

已有 Developer ID Application 身份完成实际签名，无需新建证书。最终
DMG/ZIP 已生成；从最终 DMG 提取的实际 App 通过 deep/strict 签名、
安全时间戳、hardened runtime 和最小 JIT 权限检查。连接、取消、重复
提交、沙箱/远程无桥、导航拒绝、返回及真实独立服务的成员登录、转换、
额度、历史、导出保存/取消、退出时迟到响应等流程通过。

Gatekeeper 明确返回 `Unnotarized Developer ID` 并拒绝分发验收。
这项结果没有被功能通过覆盖；没有清除隔离属性或关闭系统检查。
另外在独立 DMG 副本添加合成下载隔离属性后，实际验证工具在原生启动前
拒绝继续，阻止启动的预期测试通过；原始产物和系统策略没有修改。
在线会员状态与可用 notarytool 配置仍未核实，公证没有提交，正式安装
验收没有通过。只读元数据查询未定位到可用的现有公证 profile；这不证明
其他钥匙串或其他名称的配置不存在。操作者提供 profile name 后可继续。

本地证据与实际截图位于 `docs/evidence/desktop-ui/mac-signed-package/`；
其中 `mac-package-evidence.json` 记录实际 DMG 大小/SHA256、签名及
Gatekeeper 的独立状态。Web 适用检查仍为 657 通过/8 条件跳过，
7/7 合成格式、60 DOM 状态场景及其他项目检查通过。
