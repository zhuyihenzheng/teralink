# TeraLink（PowerShell 版）

只做一件事：用 Tera Term 打开 SSH 连接，登录后自动执行若干命令。

**没有 EXE，因此不会被 SmartScreen 拦截**，也不需要安装 .NET 运行时 —— 用 Windows 自带的
Windows PowerShell 5.1。需要另行安装官方 Tera Term 5.x（目录中要同时有 `ttermpro.exe` 和
`ttpmacro.exe`）。

## 使用

双击 `TeraLink.cmd` 列出连接并选择，或在 PowerShell 里直接调用：

```powershell
.\TeraLink.ps1                      # 列出连接，输入编号连接
.\TeraLink.ps1 web1                 # 按名称直接连接（支持部分匹配）
.\TeraLink.ps1 -Add                 # 新增连接
.\TeraLink.ps1 -Edit web1           # 修改；密码留空表示保留原密码
.\TeraLink.ps1 -Remove web1         # 删除
.\TeraLink.ps1 -List                # 只看列表
.\TeraLink.ps1 -Import              # 从旧版 C# 的 connections.json 导入 SSH 连接
.\TeraLink.ps1 -SetTeraTermPath "C:\Program Files\teraterm5\ttermpro.exe"
```

## 登录后自动执行命令

新增或编辑连接时会问两件事：

- **提示符**：登录后等待出现的字符串，例如 `$` 或 `#`。每条命令都会等到提示符再发下一条。
  留空表示不等待、也不发送任何命令。
- **命令**：一行一条，空行结束。

如果在设定的秒数内没等到提示符，会停止发送并报错，但终端保持打开，可以接着手动操作。

**命令以明文保存在 `%LOCALAPPDATA%\TeraLink\ssh.json` 里，不要把密码写进命令。** 需要
`sudo` 这类二次输入密码的场景，本版本不处理。

## 关于下载提示

`.cmd` 和 `.ps1` 都是文本文件，不会触发 SmartScreen 那个蓝色全屏拦截。但从浏览器下载的
压缩包仍带来源标记，双击 `.cmd` 可能出现一个较小的「无法验证发行者，是否运行」对话框。
**先右键压缩包 → 属性 → 解除锁定，再解压**即可避免。

`TeraLink.cmd` 用 `-ExecutionPolicy Bypass` 启动脚本。这只作用于它启动的那一个 PowerShell
进程，不修改系统执行策略。不想用这种方式的话，可以自己 `Unblock-File *.ps1`，把执行策略设为
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，然后直接跑 `.\TeraLink.ps1`。

## 密码

- 用 DPAPI 绑定**当前 Windows 账户**加密后存进 `ssh.json`，密文格式与旧版 C# 相同，所以
  `-Import` 能直接沿用旧密码，不用重新输入。
- 连接时密码通过仅本账户可读的命名管道交给本次宏进程，并核对接收端 PID。宏先建立 DDE 链接
  再接收密码；密码不出现在启动参数、宏文件、命令文件、日志或剪贴板里。
- 运行时密码仍会短暂存在内存；.NET 字符串不能保证立即擦除。DPAPI 保护不了已被控制的当前
  Windows 账户；换电脑或账户需要重新输入密码。

## 检查

```powershell
pwsh -NoProfile -File .\TeraLink.Tests.ps1     # 或 powershell -File
```

只覆盖与平台无关的逻辑：主机名校验、连接参数拼接与转义、511 字节上限、命令行校验、宏生成。
DPAPI、命名管道和 Tera Term 本身必须在 Windows 上实测，检查通过不等于端到端可用。
