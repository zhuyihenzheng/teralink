# TeraLink Python 版

**0.3.0 alpha。** 公司电脑不让运行 `TeraLink.exe` 时用这个版本：双击 `TeraLink.bat` 打开窗口，功能和 exe 版一样（Tera Term 自动登录、RDP 一键连接），另外加了**任务 / 脚本**：本地打包 WAR、上传到 Linux 服务器指定目录、在服务器上执行命令，一键跑完。

只用 Python 标准库就能启动；上传和服务器命令推荐装 `paramiko`（见下文）。

> 状态：逻辑层和界面流程有自动化测试，但**还没有在 Windows 实机上跑过**。第一次用请先拿测试服务器试。

## 需要什么

| 项目 | 说明 |
|---|---|
| Windows 10 / 11 | 密码用 Windows DPAPI 加密，只支持 Windows |
| Python 3.8 或更新 | 需要带 tkinter（官方安装包默认勾选「tcl/tk and IDLE」） |
| Tera Term 5.x | 只有「Tera Term · SSH」登录需要；目录里要同时有 `ttermpro.exe` 和 `ttpmacro.exe` |
| paramiko（可选） | 上传和服务器命令用。不装时自动改用 Windows 自带 OpenSSH，但那样只能用 SSH 密钥登录 |

### 电脑上没有 Python 怎么办

按顺序试：

1. 从 python.org 下载安装包，选 **Install for current user**（不需要管理员），勾选 *tcl/tk and IDLE*。
2. 如果安装包也被拦：在别的电脑上装好 Python，把整个 Python 文件夹（含 `pythonw.exe`、`tcl`、`Lib`）复制到本目录下，改名为 `python`，即 `.\python\pythonw.exe`。`TeraLink.bat` 会优先用它。
   注意：官方的 *embeddable zip* 不带 tkinter，不能用。
3. 公司已有 Anaconda / WinPython 等，也可以直接用，只要 `python -c "import tkinter"` 不报错。

## 开始使用

1. 把整个 `python` 文件夹复制到电脑上（不要只拷 bat）。
2. 双击 **`TeraLink.bat`**。打不开时双击 `TeraLink-debug.bat`，它会留着黑窗口显示错误。
3. 如果以前用过 exe 版，首次启动会询问是否导入旧连接。同一个 Windows 账户下密码可以直接沿用，不用重新输入。之后也可以在「连接」页点「导入旧版连接」。
4. 需要上传或服务器命令时，双击 **`install-deps.bat`** 安装 paramiko（只装到当前用户）。

### 公司网络装不了 paramiko

- 需要代理：在命令提示符里先执行 `set HTTPS_PROXY=http://代理地址:端口`，再运行 `install-deps.bat`。
- 完全离线：在一台能上网、**Python 版本相同**（例如都是 3.12 64 位）的电脑上运行 `download-wheels.bat`，把生成的 `wheels` 文件夹一起拷过来，再运行 `install-deps.bat`，它会自动离线安装。
- 都不行：任务仍可用 Windows 自带 OpenSSH（`ssh.exe` / `scp.exe`），前提是已经配置 SSH 密钥登录。窗口底部状态栏会显示当前用的是哪种 SSH 组件。

## 连接页

和 exe 版一样：

- **Tera Term · SSH**：填主机、端口（默认 22）、用户名、密码。双击或点「连接 →」，自动打开 Tera Term 并登录。首次连接请在 Tera Term 里核对主机指纹。
- **Windows 远程桌面 · RDP**：可以选你已有的 `.rdp` 文件（沿用网关、显示等设置，原文件不改），也可以手填地址。
- 搜索支持名称、地址、用户名、分组；`Ctrl+N` 新增，`Ctrl+F` 搜索，列表里按回车连接。
- 勾选「连接后退出启动器」后，连接成功会自动关闭窗口；远程会话不受影响。

## 任务 / 脚本页

一个任务由若干步骤组成，按顺序执行，任何一步失败就停止（单步可以设置“失败时继续”）。日志显示在窗口下方，同时写入日志文件。

| 步骤 | 做什么 |
|---|---|
| **本地命令/脚本** | 用 cmd 执行命令，例如 `mvn -q clean package -DskipTests`、`call build.bat`。多行依次执行，任一行失败就停止。可以点「选择脚本文件…」直接选 `.bat` / `.ps1` / `.py` |
| **生成 WAR** | 把 Web 根目录（含 `WEB-INF` 的那个）打成 `.war`，不需要 JDK 或 Maven。可设置排除，例如 `*.bak, .git` |
| **上传到服务器** | 通过 SFTP 把本地文件传到 Linux 服务器的指定目录，目录不存在会自动创建 |
| **服务器命令** | 在服务器上执行命令并显示输出，例如重启服务、查看日志。走的是非登录 shell，环境变量可能比 Tera Term 里少，需要时写成 `bash -lc '…'` |

上传和服务器命令使用「连接」页保存的 **SSH 连接**（账号密码），不需要另外配置。

### 变量

在步骤里可以写这些占位符，运行时会替换：

| 变量 | 含义 |
|---|---|
| `${ARTIFACT}` | 上一步生成的 WAR，或上一次上传的本地文件 |
| `${REMOTE_FILE}` | 上一次上传到服务器后的完整路径 |
| `${NOW}` | 时间戳，例如 `20261006-153000` |
| `${TODAY}` | 日期，例如 `20261006` |
| `${TASK}` | 任务名 |

只替换上面这几个。服务器命令里的 `$HOME`、`${JAVA_HOME}` 这类 shell 写法原样保留。

上传的“本地文件”支持通配符：`C:\work\myapp\target\*.war` 有多个匹配时取最新的一个。

### 模板

「从模板新建」提供：

- **Maven 打包并部署到 Tomcat**：`mvn package` → 上传 `target\*.war` 到 `/opt/tomcat/webapps/myapp.war` → 等 5 秒后看 `catalina.out`
- **目录打包 WAR 并上传**：直接把 `WebContent` 打成 WAR 再上传
- **运行本地脚本**
- **服务器命令（重启服务）**

模板里的路径都是示例，保存前改成你自己的。

### 上传的安全措施

- **先传临时文件再改名**：先上传为隐藏的 `.myapp.war.part`，传完再改成 `myapp.war`。Tomcat 等自动部署程序不会读到传了一半的文件。
- **覆盖前备份**（默认勾选）：服务器上已有同名文件时，先改名为 `myapp.war.bak-20261006-153000`。备份不会自动清理，请定期手动删除。
- **服务器指纹确认**：第一次连接某台服务器时弹窗显示 SHA256 指纹，确认后保存。以后指纹变化会直接拦截，提示可能是服务器重装或中间人攻击。
- **运行前确认**（默认勾选）：运行前列出所有步骤和目标服务器，确认后才执行。
- 服务器命令不能交互输入。需要 `sudo` 时请用 `sudo -n`，并让管理员为该命令配置免密，否则会卡在密码提示直到你点「■ 停止」。

### 分享任务给同事

「导出任务…」会生成一个 JSON 文件，**不含任何密码**。服务器按连接名称记录。同事「导入任务…」后，会自动对应到他本机同名的连接；对不上的会提示，需要手动选一下。

## 数据保存在哪

| 路径 | 内容 |
|---|---|
| `%LOCALAPPDATA%\TeraLinkPy\data.json` | 连接和任务。密码用 Windows DPAPI 按当前用户加密 |
| `%LOCALAPPDATA%\TeraLinkPy\known_hosts` | 已确认的服务器指纹 |
| `%LOCALAPPDATA%\TeraLinkPy\logs\` | 每天一个执行日志；出错时还有 `crash-*.log` |
| `%LOCALAPPDATA%\TeraLinkPy\rdp\` | 启动 RDP 时生成的连接文件（密码已加密） |

exe 版的数据（`%LOCALAPPDATA%\TeraLink\`）不会被修改。两个版本可以共存。

## 安全说明

- 密码不以明文落盘。换电脑或换 Windows 账户后需要重新输入密码。
- Tera Term 登录沿用 exe 版的做法：密码通过只允许当前 Windows 用户访问的命名管道传给本次启动的宏，并核对接收进程的 PID。密码不出现在命令行参数、宏文件、日志或剪贴板里。
- 日志只记录命令和输出，不记录密码。但服务器命令的输出会原样写进日志，不要在命令里直接写密码。
- 同时只能打开一个 TeraLink 窗口，避免两个窗口互相覆盖数据。

## 常见问题

| 现象 | 处理 |
|---|---|
| 双击 bat 闪一下就没了 | 运行 `TeraLink-debug.bat` 看错误 |
| 提示没有 tkinter | 重新运行 Python 安装包，选 Modify，勾选 *tcl/tk and IDLE* |
| `python` 打开了 Microsoft Store | 那是系统的占位快捷方式，不是真的 Python。按上文安装 Python |
| 上传时“登录失败” | 确认服务器允许密码登录（`PasswordAuthentication yes`），并检查连接页保存的密码 |
| 指纹不一致被拦截 | 先向管理员确认服务器是否重装过；确认无误后，从 `known_hosts` 删除那一行再试 |
| 本地命令输出乱码 | 本工具按系统默认编码读取输出；可在命令前加 `chcp 65001 >nul &&` 改为 UTF-8 |
| 装了 paramiko 仍提示用 OpenSSH | 公司可能拦截了 Python 的扩展库（`.pyd`）。运行 `TeraLink-debug.bat`，再执行 `python -c "import paramiko"` 看报错 |

## 开发

```bat
cd python
python -m unittest discover -s tests
```

测试不需要 Windows、Tera Term、paramiko 或 tkinter：界面测试使用一个假的 tkinter 跑通所有按钮和对话框的逻辑，但不检查实际布局。

代码结构：

| 文件 | 作用 |
|---|---|
| `teralink/model.py` | 数据结构、校验、数据文件读写、旧版导入 |
| `teralink/vault.py` | DPAPI 加密（ctypes） |
| `teralink/teraterm.py` · `winpipe.py` | Tera Term 宏和安全命名管道 |
| `teralink/rdp.py` | RDP 连接文件 |
| `teralink/remote.py` | SSH / SFTP（paramiko，或 Windows OpenSSH） |
| `teralink/tasks.py` | 任务执行、WAR 打包、模板 |
| `teralink/ui.py` | tkinter 界面 |
