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
