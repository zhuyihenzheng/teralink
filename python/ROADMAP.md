# Python 版：现状与后续规划

写给接手的人（包括在本地运行的 Claude Code）。先读这一页，再读 [README.md](README.md)。

## 背景

- 公司电脑不能运行 `TeraLink.exe`，原因**尚未确认**（可能是 SmartScreen 标记、AppLocker/WDAC 白名单或杀毒误报）。
- 因此做了 Python 版：双击 `TeraLink.bat` 启动 tkinter 界面，功能与 exe 版相同，另加“任务/脚本”（本地命令、生成 WAR、SFTP 上传到 Linux、服务器命令）。
- 开发环境是云端 Linux 容器，**没有在 Windows 实机运行过**。PyPI 被网络策略拦截，paramiko 和 tkinter 都没装上：界面靠假 tkinter 冒烟测试，上传靠假 SSH 会话测试。

## 现状（0.3.0 alpha）

GitHub Actions（`.github/workflows/python.yml`）在每次推送时自动验证：

| 部分 | 怎么验证的 | 结果 |
|---|---|---|
| 数据模型、校验、旧版导入、WAR 打包、任务执行 | 单元测试（Linux / macOS / Windows） | ✅ |
| DPAPI 加密、`msvcrt` 单实例锁、cmd 多行命令与中文输出、停止任务（taskkill） | windows-latest，Python 3.8 与 3.12 | ✅ |
| 命名管道：仅当前用户、PID 校验、同名拒绝、无人连接时关闭不卡死 | windows-latest | ✅ |
| 真 Tera Term 5.7：宏通过 DDE 附着并从管道读到连接命令，会话目录被清理 | windows-latest（官方 portable zip） | ✅ |
| RDP 连接文件：UTF-16 BOM、`password 51` 加密 | windows-latest（mstsc 调用被替换） | ✅ |
| 真 tkinter：主窗口和所有对话框能建出来、能保存 | windows-latest + macOS；截图在 Actions 的 `windows-screenshots` | ✅ |
| paramiko：指纹确认一次后记住（非 22 端口）、拒绝指纹、错误密码、上传+备份+改名、上传中途停止不留 `.part`、WAR→上传→远程命令整条任务 | ubuntu-latest 上真实 sshd | ✅ |

仍未验证（需要公司电脑或 Windows 虚拟机）：

- Tera Term **真正登录成功**（含首次主机指纹弹窗）和输错密码时的表现
- mstsc 真正连上远程桌面
- 公司代理 / 白名单环境下 `install-deps.bat` 和 paramiko 能否加载
- 日文 / 中文 Windows 上本地命令输出编码（CI 是英文系统）

运行测试：`cd python && python -m unittest discover -s tests`。设置 `TERALINK_SSH_TEST_HOST` 等环境变量可对真实服务器跑 `test_integration_ssh.py`（见文件开头说明）。

### 已知限制

- 只支持 **Tera Term 5.x**。chocolatey 等渠道默认仍是 4.108，公司电脑上很可能是 4.x，届时会提示版本不符。是否放宽到 4.x 需要先验证 4.x 的宏编码（报告路径含非 ASCII 用户名时）。

## Windows 实机验收清单

1. 双击 `TeraLink.bat` 能打开窗口，中文字体正常，高 DPI 不模糊。
2. 新增 SSH 连接 → 连接 → Tera Term 自动登录成功；输错密码时不会重复提交。
3. 新增 RDP 连接（手填地址，以及选已有 `.rdp` 文件两种）→ mstsc 打开且带上账号。
4. 导入 exe 版数据，导入后的密码能直接登录。
5. `install-deps.bat` 能装上 paramiko；状态栏显示 `paramiko x.y`。
6. 任务：Maven 模板 → 上传 → 服务器命令，全程日志正常；第一次连接弹出指纹确认。
7. 上传过程中点“停止”，服务器上不残留 `.part` 文件。
8. 本地命令输出中文不乱码（日文/中文系统各看一次）。

## 待确认：exe 被拦的原因

在公司电脑上执行，把结果记下来：

1. 双击 exe 时的报错原文。
2. 右键 exe → 属性，最下方是否有“解除锁定”。勾选后 exe 若能运行，问题就解决了。
3. 命令提示符运行：

```bat
powershell -NoProfile -Command "$ExecutionContext.SessionState.LanguageMode"
where python py ssh scp ttermpro winscp
```

- 输出 `FullLanguage`：可以考虑 PowerShell + WinForms 版，不依赖 Python。
- 输出 `ConstrainedLanguage`：说明有白名单策略，PowerShell 方案也走不通，继续用 Python 版或公司已批准的软件（WinSCP、IntelliJ 等）。

## 下一步：通用性与可扩展性

当前的限制：步骤类型写死在代码里；任务存在个人数据文件；只能点界面；连接类型只有 SSH/RDP。

目标结构：

```
核心引擎（不依赖界面）
├─ 连接类型注册表：ssh / rdp / 以后可加跳板机等
├─ 步骤类型注册表：local / war / upload / remote，外加 plugins/ 里自定义的 .py
├─ 任务文件：项目仓库里的 teralink.json，跟着代码走，团队共用
└─ 密钥来源：Windows DPAPI，以后可接公司密码库
    ↓ 同一个引擎，三种入口
GUI（tkinter）   CLI（teralink run 部署 --env 测试）   CI / 计划任务调用
```

建议的实施顺序：

1. **抽出 CLI**：`python -m teralink run <任务名>`、`list`、`connect <连接名>`。`tasks.Runner` 已经不依赖界面，主要是加参数解析和终端版的指纹确认。
2. **步骤注册表**：把 `tasks.Runner._run_step` 里的 if/elif 改成 `{类型: 处理类}`，每个类负责校验、执行和界面表单定义；`plugins/*.py` 启动时自动加载。
3. **项目内任务文件**：支持从项目目录读取 `teralink.json`（与个人数据合并显示，密码仍只存在本机）。连接按名称引用，和现在的导出格式一致。
4. **多环境**：同一任务配“测试 / 正式”两组变量（服务器、路径），运行时选择；正式环境强制确认。
5. 视验收结果决定是否做 PowerShell 版。

## 相关文件

- 代码结构见 [README.md 的“开发”一节](README.md#开发)。
- C# 原版在 `../src/`，行为以它为准（尤其是 Tera Term 宏和 RDP 文件处理）。
