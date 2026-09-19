# TeraLink — Tera Term / RDP 快捷登录

**0.2.0 alpha，Windows x64。** 保存连接和加密密码，打开 Tera Term 自动提交 SSH 登录，或使用 Windows 自带 `mstsc.exe` 打开远程桌面。支持沿用已有 `.rdp` 文件。

[下载 Windows 版（预发布）](https://github.com/zhuyihenzheng/teralink/releases/tag/v0.2.0-alpha) · [源码仓库](https://github.com/zhuyihenzheng/teralink)

**同一次 RDP 连接中出现的第二层 user／密码登录尚未实现自动填写。** 当前只向 RDP 客户端提供一组登录凭据；不把第二层当成第二次 RDP。需要根据实际第二层画面确定后续实现。Windows 真实登录、界面和资源占用尚未实机验证。

## 开始使用

- **轻量包 `TeraLink-win-x64-lite.zip`**：完整解压后运行 `TeraLink.exe`。电脑需已有 **.NET 10 Windows Desktop Runtime x64**（仅装普通 .NET Runtime 不够）。
- **自带运行时包 `TeraLink-win-x64.zip`**：完整解压后运行，无需另装 .NET。不要只取出 EXE。
- Tera Term 另行安装，使用官方 5.x，目录中需同时有 `ttermpro.exe` 和 `ttpmacro.exe`。RDP 使用系统客户端，无需安装 Tera Term。
- **只用 SSH 的话**：`ps/` 下有一个不含 EXE 的 PowerShell 版本，只做 Tera Term 连接和登录后自动执行命令，不触发 SmartScreen，也不需要 .NET 运行时。见 [ps/README.md](ps/README.md)。

### 下载后先解除锁定

本工具**没有代码签名**。浏览器下载的 ZIP 会被 Windows 打上来源标记，解压时这个标记会传给解压出来的每个文件，运行 `TeraLink.exe` 就会出现蓝色的「Windows 已保护你的电脑」。**先解除锁定、再解压**可以避免这个提示：

1. 右键点击下载好的 `.zip` → **属性** → 勾选底部的 **解除锁定** → 确定。
2. 然后再解压，运行 `TeraLink.exe`。

PowerShell 等效操作：

```powershell
Get-FileHash .\TeraLink-win-x64.zip -Algorithm SHA256   # 先与发布页公布的哈希核对
Unblock-File .\TeraLink-win-x64.zip
```

已经解压过的，对解压目录执行 `Get-ChildItem -Recurse | Unblock-File` 同样有效。

解除锁定等于跳过 Windows 的来源检查，**请只对核对过 SHA256 的文件这么做**。已经看到提示时，点 **更多信息 → 仍要运行** 也可以继续，确认过一次后不再重复询问。

Windows 11 开启**智能应用控制（Smart App Control）** 时，未签名程序会被直接阻止，解除锁定无效；只能等代码签名，或在系统设置中关闭该功能（关闭后除非重装系统否则无法再开启）。

### 使用已有远程桌面连接

1. 新增连接，类型选择 **Windows 远程桌面 · RDP**。
2. 点击 **选择已有 .rdp**，选择平时使用的远程桌面连接文件。若手里只有快捷方式 `.lnk`，请在远程桌面客户端里使用“另存为”保存成 `.rdp`。
3. 填写登录用户名和密码。域账户可用 `DOMAIN\user` 或 `user@domain`；若原文件已有账户，会自动填入。
4. 保存后双击连接，或点击 **连接 →**。

原文件保持不变，副本保留原文件的网关、路由、显示、设备重定向等设置，仅替换登录用户名、密码和客户端凭据提示选项。输入完整域账户时移除原域字段，输入短用户名时保留原域。修改后的副本不保留失效的数字签名；Windows 仍可能提示未签名连接文件，组织策略也可能禁止使用。原文件设置改变后需重新选择并保存，避免向未经重新确认的目标传递密码。

不使用已有文件时，可直接填主机地址和端口（默认 3389），选择是否全屏。这种模式默认关闭壁纸、菜单动画、打印机、磁盘和音频重定向，保留剪贴板。使用已有文件时，以原文件选项为准。

RDP 的“已打开”仅表示客户端启动，**不表示已登录成功**。客户端或服务器策略仍可能要求再次输入密码；工具不修改组织策略，不自动确认服务器证书，也不处理动态验证码或桌面内的第二层认证。

### Tera Term 登录

新增连接选择 **Tera Term · SSH**，填写主机、端口（默认 22）、用户名、密码后保存。连接时自动寻找 Tera Term，也可手动选择路径。首次连接需在 Tera Term 核对主机指纹。

密码通过仅允许当前 Windows 用户的本机命名管道传给本次宏进程，并核对客户端 PID。宏先建立 DDE 链接，再接收并提交密码；密码不写入启动参数、宏文件、日志或剪贴板。

Tera Term 宏连接命令最多 511 个 UTF-8 字节，不支持控制字符；每次点击只提交一次，等待上限 120 秒。“停止等待”只终止本次宏助手，保留已打开终端。宏报告连接完成不代表工具验证了远程 shell。

## 轻量化

- Windows Forms 原生桌面控件，无浏览器内核、后台服务、托盘常驻或定时扫描。
- Tera Term 宏等待改为进程事件和异步管道，不再每 100 毫秒轮询。
- 勾选 **连接后退出启动器**：RDP 客户端启动后、SSH 宏完成后自动退出 TeraLink；远程会话继续运行。失败时保留窗口显示错误。关闭工具也不会关闭已有远程会话。
- Lite 包减少分发和磁盘体积，**不代表相同运行状态下内存更少**。CPU、工作集和私有内存尚未 Windows 实测，不宣称具体数值。Tera Term / mstsc 自身仍消耗资源。

## 数据与密码

- `%LOCALAPPDATA%\TeraLink\connections.json`：保存连接和设置，密码使用 Windows DPAPI 当前用户范围加密。
- `%LOCALAPPDATA%\TeraLink\rdp\<连接ID>.rdp`：启动 RDP 时生成，密码为 DPAPI 加密的 UTF-16LE 数据（`password 51`）。文件保留供 mstsc 在启动器退出后读取；编辑或删除连接时删除对应副本。请保留自己选择的原 `.rdp` 文件。
- 不向共享 `TERMSRV` 凭据写入密码，不覆盖系统里同一主机的其他账户。不同连接使用不同副本。
- 密码不以明文落盘。运行时密码仍会短暂存在内存；.NET 字符串不能保证立即擦除。DPAPI 不能保护已被控制的当前 Windows 账户；换电脑或账户时通常需要重新输入密码。
- 编辑密码留空表示保留原密码。搜索支持名称、地址、用户名、分组、SSH/RDP；`Ctrl+N` 新增，`Ctrl+F` 搜索，列表 `Enter` 连接。
- 旧版 v1 SSH 数据读取后升级为 v2，原加密密码保持不变；旧版工具会拒绝读取新版保存的数据，避免把 RDP 当作 SSH。
- 同时只允许一个实例写连接库；原子保存，文件损坏时报错，不自动重置。无云同步或遥测。

## 构建与验证

需要 .NET 10 SDK：

```powershell
dotnet run --project tests/TeraLink.Checks -c Release
.\build.ps1                 # 默认：lite 包，依赖已安装的 Desktop Runtime
.\build.ps1 -SelfContained  # 完整包，自带运行时
# ARM64：追加 -Runtime win-arm64
```

构建结束会打印 ZIP 的 SHA256，并写入同名 `.sha256` 文件，发布时请一并公布。持有代码签名证书时追加 `-CertificateThumbprint <指纹>`，打包前会用 Windows SDK 的 `signtool` 对 `TeraLink.exe` 和自身的 DLL 做 SHA256 签名并加时间戳；不传该参数则产出未签名包。签名是消除 SmartScreen 提示的唯一根本手段，解除锁定只是绕开本机的来源标记。

详见 [验证记录](QA.md) 和 [Windows 验收清单](WINDOWS-ACCEPTANCE.md)。构建通过不等于 Windows 端到端通过。

实现依据：[Tera Term 密码参数与 DDE 说明](https://teratermproject.github.io/manual/5/en/commandline/ttssh.html)、[TTL connect](https://teratermproject.github.io/manual/5/en/macro/command/connect.html)、[Microsoft mstsc](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/mstsc)、[RDP 设置](https://learn.microsoft.com/en-us/azure/virtual-desktop/rdp-properties)、[DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。
