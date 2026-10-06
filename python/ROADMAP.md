# Python 版：现状与后续规划

写给接手的人（包括在本地运行的 Claude Code）。先读这一页，再读 [README.md](README.md)。

## 背景

- 公司电脑不能运行 `TeraLink.exe`，原因**尚未确认**（可能是 SmartScreen 标记、AppLocker/WDAC 白名单或杀毒误报）。
- 因此做了 Python 版：双击 `TeraLink.bat` 启动 tkinter 界面，功能与 exe 版相同，另加“任务/脚本”（本地命令、生成 WAR、SFTP 上传到 Linux、服务器命令）。
- 开发环境是云端 Linux 容器，**没有在 Windows 实机运行过**。PyPI 被网络策略拦截，paramiko 和 tkinter 都没装上：界面靠假 tkinter 冒烟测试，上传靠假 SSH 会话测试。

## 现状（0.3.0 alpha）

| 部分 | 状态 |
|---|---|
| 数据模型、校验、旧版数据导入 | 单元测试覆盖 |
| WAR 打包、任务执行、停止、多行命令 | 单元测试覆盖（Linux） |
| 界面流程（新增/编辑/删除、模板、导出导入、运行） | 假 tkinter 冒烟测试，布局未看过 |
| DPAPI、命名管道、Tera Term 宏、mstsc | 只按 C# 版逐行移植，**未在 Windows 运行** |
| paramiko 上传和服务器命令 | 只测了调用流程，**未连过真实服务器** |

运行测试：`cd python && python -m unittest discover -s tests`

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
