# 验证记录

日期：2026-09-12。版本：0.2.0 alpha。环境：macOS ARM64，.NET SDK 10.0.401。

## 已执行

- `dotnet run --project tests/TeraLink.Checks -c Release`：24 项通过。新增 RDP IPv6/端口、用户名及加密字段注入拒绝、原 RDP 设置保留、域用户名导入、重复设置及目标改变拒绝、连接类型隔离、v1 SSH 数据迁移、退出偏好持久化检查。原 SSH 参数转义、UTF-8 长度边界、文件损坏保留和单写入者检查继续通过。
- Windows 项目 Release 构建：0 错误、0 警告。
- Windows x64 lite（framework-dependent）及 self-contained 发布，并打包源码。
- 审阅 RDP 密码传递：应用 DPAPI UTF-8 密文解密后，以 UTF-16LE 编码重新通过当前用户 DPAPI 加密，输出十六进制 `password 51` 字段；进程参数仅有派生文件路径。

## 尚未执行 / 未实现

- DPAPI 的 Windows 加解密、篡改拒绝和 RDP UTF-16LE 密码编码检查：非 Windows 平台明确跳过，需 Windows 运行检查程序。
- mstsc 对生成文件密码的接受情况、现有 RDP 配置的真实兼容性及实际登录。公司策略、签名要求或再次询问密码仍可能阻止自动登录。
- 同一次 RDP 的第二层 user／密码自动填写：未实现，等待确认实际画面。
- Tera Term 实际读取命名管道、DDE 连接、宏退出/超时/取消竞争、真实 SSH 登录。
- Windows Forms 布局、滚动、输入法、DPI 和启动器自动退出。
- Windows CPU、工作集、私有内存：未测量。Lite 包体积不可视为内存优化数据。

当前不具备 Windows GUI 运行环境。代码检查和交叉编译不能替代实机验收。

## 2026-09-18 公开发布检查

- 已核对现有 Windows 完整包、轻量包及源码包的 SHA256 和 ZIP 完整性；源码包中的 C# 文件与当前源码一致。
- 本次公开发布沿用 2026-09-12 的构建产物。尝试重新运行检查时，临时目录中的 .NET SDK 已缺少 `Sdk.props` 等组件，未完成重新构建或测试；不把此前的通过记录视为本次重新执行。
- 排除构建缓存、运行时连接数据和本地环境文件。现有 Windows 实机验收限制保持不变。

## 2026-09-18 SmartScreen 拦截处理

- 症状确认为蓝色「Windows 已保护你的电脑」，即 SmartScreen 对未签名、无声誉且带来源标记（MOTW）的可执行文件的拦截，与实现语言无关。
- README 增加「下载后先解除锁定」说明：先对 ZIP 解除锁定再解压可避免提示，并要求先核对 SHA256；说明 Smart App Control 下解除锁定无效。
- 补齐 Win32 版本资源（`Product`、`Company`、`Copyright`、`AssemblyTitle`、`Description`、`FileVersion`），`app.manifest` 版本由 1.0.0.0 对齐为 0.2.0.0。
- `build.ps1` 增加可选 `-CertificateThumbprint` 签名步骤（`signtool` SHA256 + 时间戳，签名后校验），未签名时输出警告；ZIP 的 SHA256 额外写入 `.sha256` 文件。
- **本次未执行任何构建或测试。** 当前环境没有 .NET SDK 也没有 PowerShell，`build.ps1` 未做语法检查，版本资源与签名流程需在 Windows 上重新验证。
- 代码签名证书尚未获取；在签名之前，解除锁定和「更多信息 → 仍要运行」是仅有的规避方式，不能替代签名。

## 2026-09-19 PowerShell 版（ps/）

新增不含 EXE 的实现，只做 Tera Term SSH 连接和登录后自动执行命令；现有 C# 实现未改动。没有编译产物即没有 SmartScreen 拦截，也不需要 .NET 运行时，目标是 Windows 自带的 Windows PowerShell 5.1。

- 沿用 C# 的密码路径：DPAPI 当前用户范围加密，密文格式相同（`ProtectedData.Protect` 与 `CryptProtectData` 一致），`-Import` 可直接读取旧 `connections.json` 中的 SSH 连接且不改动原文件；密码经仅本账户 ACL 的命名管道传给本次宏进程，并用 `GetNamedPipeClientProcessId` 核对 PID；宏先建立 DDE 链接再接收密码。
- 新增登录后自动执行命令：命令与提示符写入会话目录下的独立文件，由宏在 `connect` 成功后逐条 `wait` + `sendln`；命令不是秘密，但**以明文保存**，已在文档中说明不要写入密码。
- 已执行：`pwsh 7.4.6 (Linux)` 下 `ps/TeraLink.Tests.ps1` 34 项通过，覆盖主机名校验、连接参数拼接与引号转义、511 UTF-8 字节边界、命令行控制字符拒绝、命令文件格式、宏生成与伪造管道名/路径引号拒绝。三个 `.ps1` 均带 UTF-8 BOM，避免 Windows PowerShell 5.1 按 ANSI 解码中文。
- **尚未执行**：Windows 上的任何实机运行。DPAPI 往返、`PipeSecurity` 构造、`Add-Type` P/Invoke、`Process.Start` 与 `Handle` 等待、ttpmacro 实际读取管道与命令文件均未验证。
- **风险最高的未验证点**：宏中 `wait prompt`（对字符串变量求值）和 `filereadln` 逐行读取命令文件的行为，均依据 Tera Term 宏文档推断，需在真实 Tera Term 5.x 上确认。提示符等待失败时报 `commands-timeout` 并保留终端。
- Windows PowerShell 5.1 的默认执行策略为 Restricted，因此入口 `TeraLink.cmd` 使用 `-ExecutionPolicy Bypass`，仅作用于其启动的单个进程，不修改系统策略；文档同时给出不使用该方式的替代步骤。
