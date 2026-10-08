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
会员状态和续费日期。已授权的 notarytool 配置可以验证公证服务访问，
但不能代替账户页面的会员期限核实。
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

先完成账户核实，再准备认证，最后单独确认上传具体产物。
这三个步骤不会由签名构建自动触发。

1. 使用已授权的浏览器访问
   [Apple Developer 账户](https://developer.apple.com/account/)。优先复用
   已打开的官方账户页面，不操作其他标签或重启浏览器。如果页面要求登录，
   将控制交给操作者，由其输入 Apple Account、密码和双重认证；完成后只需
   告知“登录完成”，不在聊天中发送任何认证信息。
2. 工程流程在恢复浏览器访问后只读查看 Membership details，核对
   Team ID **NRUX9D4Z3Z**、会员状态、角色和续费日期。该 Team ID 来自本次
   已实际签名的公开证书，不需要操作者猜测团队。如果页面未显示该团队，
   或工具无法读取页面，继续记录为未核实，先处理账户访问问题。
   [Apple 对账户页面字段的说明](https://developer.apple.com/help/account/basics/account-landing-page/)
3. 如果操作者已有公证配置，只提供 profile name；工程流程用该名称验证
   服务访问，不读取凭据内容。本次项目配置、`APPLE_KEYCHAIN_PROFILE`
   以及默认查询范围内的 `markitdown-notary` 元数据均未找到配置；这不证明
   其他名称或其他钥匙串没有配置。
4. 确认需要新建配置后，操作者自行登录
   [Apple Account](https://account.apple.com/)，进入“登录与安全性 / Sign-In
   and Security” → “应用专用密码 / App-Specific Passwords” → “生成 / Generate”。
   可用名称 `MarkItDown notarization`。生成需要双重认证；操作者自行处理
   账户验证和生成的秘密，不把页面内容或密码交给工程流程。
   [Apple 应用专用密码步骤](https://support.apple.com/en-us/102654)
5. 操作者确认保存到本机钥匙串后，在自己的交互式终端执行下列命令，
   将占位邮箱换为刚核实团队所用的 Apple Account。工程流程不会代为执行。

```sh
xcrun notarytool store-credentials "markitdown-notary" \
  --apple-id 'YOUR_APPLE_ACCOUNT_EMAIL' \
  --team-id NRUX9D4Z3Z
```

命令会在安全终端提示中询问**应用专用密码**，默认向 Apple 验证后写入
钥匙串；没有 `--password` 参数，因此密码不进入命令行或 shell 历史。
不要使用 Apple Account 登录密码，也不要添加 `--no-validate`。如果该名称
已经存在，命令会更新它；先确认它属于本项目，避免覆盖其他用途的配置。
完成后只回复 profile name 和“验证并保存成功”。不发送密码、API 私钥或
验证码，不把凭据写入仓库、普通文档或截图。本次没有生成或保存任何凭据。

工程流程获得该名称及只读验证授权后，可以使用
`xcrun notarytool history --keychain-profile "markitdown-notary"` 验证服务访问。
成功后，再让操作者确认将以下**已有签名 DMG**上传 Apple 公证服务：

- 路径：`packages/markitdown-desktop/dist/developer-id/MarkItDown-1.1.0-dev.1-mac-arm64-developer-id-unnotarized.dmg`
- 本次上传前字节数：`127127799`
- 本次上传前 SHA256：`27f4265e64a6066e15adf626be3d5c2872f86e410f8bddc2da6c8480737f91a7`

上传授权与认证条件都明确后才提交，等待 Accepted，对产物 staple/validate，
重新检查 Gatekeeper 和下载包。未收到 Accepted
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
其他钥匙串或其他名称的配置不存在。

初次读取本机 Chrome 官方账户页元数据的 AppleEvent 查询超时（`-1712`）。
后续查询恢复时，Chrome 的四个窗口中没有找到官方 Apple 账户/开发者标签。
按操作者定位登录入口的要求，仅在已有窗口新建了后台官方账户入口，保留
原有活动标签和前台；尚未读取新页面的登录状态或任何输入框。随后操作者
明确将公证推迟到明天，账户与公证方向已暂停。当前没有启动凭据录入命令，
没有需要本流程立即处理的终端密码提示。会员、profile 和公证均仍待核实。

当前执行环境没有完整浏览器控制/登录交接工具，也没有发现现成的 Chrome
调试端口。没有重启用户 Chrome、开启调试或更改浏览器/系统安全设置，
也没有读取 cookies、账户令牌、密码、私钥或转储钥匙串。

本地证据与实际截图位于 `docs/evidence/desktop-ui/mac-signed-package/`；
其中 `mac-package-evidence.json` 记录实际 DMG 大小/SHA256、签名及
Gatekeeper 的独立状态。Web 适用检查仍为 657 通过/8 条件跳过，
7/7 合成格式、60 DOM 状态场景及其他项目检查通过。
`auth-handoff.json` 记录后续只读认证检查范围及浏览器交接阻塞。

后续 CI 暴露了既有队列的写锁竞争：工作线程在取得写锁前记录开始时间，
上传可能先提交，导致新任务开始时间早于创建时间。独立回归先稳定复现，
修复为取得写锁后采样，未放宽原有断言。新增覆盖后本地 Web 检查为
658 通过/8 条件跳过；最终签名 DMG 与修复后的临时服务再次通过原生流程，
Gatekeeper 仍按未公证拒绝。复测证据位于
`docs/evidence/desktop-ui/mac-signed-recheck/`。
