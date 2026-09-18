using TeraLink.Core;

namespace TeraLink.Windows;

internal sealed class ConnectionDialog : Form
{
    private readonly TextBox name = new() { MaxLength = 100 };
    private readonly TextBox host = new() { PlaceholderText = "例如 192.168.1.10 或 server.example.com", MaxLength = 253 };
    private readonly NumericUpDown port = new() { Minimum = 1, Maximum = 65535, Value = 22 };
    private readonly ComboBox kind = new() { DropDownStyle = ComboBoxStyle.DropDownList };
    private readonly CheckBox fullScreen = new() { Text = "远程桌面全屏", AutoSize = true };
    private readonly TextBox rdpFile = new() { ReadOnly = true, PlaceholderText = "可选：选择你已有的 .rdp 文件" };
    private string rdpFileHash = "";
    private readonly TextBox username = new() { MaxLength = 255 };
    private readonly TextBox password = new() { UseSystemPasswordChar = true, MaxLength = 1024 };
    private readonly TextBox group = new() { MaxLength = 100, PlaceholderText = "例如 开发环境 / 生产环境" };
    private readonly TextBox notes = new() { Multiline = true, Height = 70, MaxLength = 2000, ScrollBars = ScrollBars.Vertical };
    private readonly CheckBox favorite = new() { Text = "收藏此连接", AutoSize = true };
    private readonly Connection? original;
    public Connection? Result { get; private set; }

    public ConnectionDialog(Connection? connection = null)
    {
        original = connection;
        Text = connection is null ? "新增连接" : "编辑连接";
        ClientSize = new Size(550, 740);
        MinimumSize = new Size(540, 600);
        StartPosition = FormStartPosition.CenterParent;
        MaximizeBox = false; MinimizeBox = false;
        BackColor = Color.White;
        AutoScaleMode = AutoScaleMode.Dpi;
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(26), ColumnCount = 1, RowCount = 3 };
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.Controls.Add(Ui.Label(connection is null ? "添加你的服务器" : "更新连接信息", 18, true));
        var fields = new TableLayoutPanel { Dock = DockStyle.Fill, AutoScroll = true, ColumnCount = 2, Padding = new Padding(0, 15, 0, 10) };
        fields.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105));
        fields.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        void Field(string label, Control input)
        {
            int row = fields.RowCount++;
            fields.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            fields.Controls.Add(new Label { Text = label, AutoSize = true, Padding = new Padding(0, 6, 0, 0), ForeColor = Ui.Muted }, 0, row);
            input.Dock = DockStyle.Top; input.Margin = new Padding(0, 0, 0, 16);
            fields.Controls.Add(input, 1, row);
        }
        kind.Items.AddRange(["Tera Term · SSH", "Windows 远程桌面 · RDP"]);
        kind.SelectedIndex = (int)(connection?.Kind ?? ConnectionKind.Ssh);
        fullScreen.Checked = connection?.RdpFullScreen ?? false;
        fullScreen.Enabled = kind.SelectedIndex == 1;
        rdpFile.Text = connection?.RdpFilePath ?? "";
        rdpFileHash = connection?.RdpFileHash ?? "";
        var fileActions = new FlowLayoutPanel { AutoSize = true, WrapContents = true };
        fileActions.Controls.Add(Ui.Button("选择已有 .rdp", () =>
        {
            using var picker = new OpenFileDialog { Filter = "远程桌面连接 (*.rdp)|*.rdp", CheckFileExists = true, Multiselect = false };
            if (picker.ShowDialog(this) != DialogResult.OK) return;
            try
            {
                string profile = RdpLauncher.ReadProfile(picker.FileName);
                var settings = RdpProfile.Inspect(profile);
                rdpFile.Text = picker.FileName; rdpFileHash = RdpLauncher.Fingerprint(profile);
                host.Text = settings.Host; port.Value = settings.Port;
                if (settings.Username.Length > 0) username.Text = settings.Username;
                if (name.Text.Length == 0) name.Text = Path.GetFileNameWithoutExtension(picker.FileName);
                UpdateSourceControls();
            }
            catch (Exception error) { Ui.Error(this, error); }
        }));
        fileActions.Controls.Add(Ui.Button("改为手填地址", () => { rdpFile.Clear(); rdpFileHash = ""; UpdateSourceControls(); }));
        void UpdateSourceControls()
        {
            bool isRdp = kind.SelectedIndex == 1;
            bool useFile = isRdp && rdpFile.Text.Length > 0;
            rdpFile.Enabled = fileActions.Enabled = isRdp;
            host.Enabled = port.Enabled = !useFile;
            fullScreen.Enabled = isRdp && !useFile;
        }
        kind.SelectedIndexChanged += (_, _) =>
        {
            if (kind.SelectedIndex == 1 && port.Value == 22) port.Value = 3389;
            if (kind.SelectedIndex == 0 && port.Value == 3389) port.Value = 22;
            UpdateSourceControls();
            username.PlaceholderText = kind.SelectedIndex == 1 ? @"例如 DOMAIN\user 或 user@domain" : "SSH 用户名";
        };
        Field("连接类型", kind);
        Field("已有 RDP 文件", rdpFile); Field("", fileActions);
        Field("连接名称 *", name); Field("主机地址 *", host); Field("端口", port);
        Field("用户名 *", username); Field("密码 *", password);
        var reveal = new CheckBox { Text = "显示本次输入的密码", AutoSize = true };
        reveal.CheckedChanged += (_, _) => password.UseSystemPasswordChar = !reveal.Checked;
        Field("", reveal); Field("", fullScreen); Field("分组", group); Field("备注", notes); Field("", favorite);
        var hint = Ui.Label(connection is null ? "密码经 Windows 账户加密，仅保存在本机。" : "密码留空会保留原密码；填写新密码即可替换。", 9);
        hint.ForeColor = Ui.Muted; hint.MaximumSize = new Size(350, 0);
        Field("", hint);
        root.Controls.Add(fields);
        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft, WrapContents = false, Padding = new Padding(0, 14, 0, 0) };
        var save = Ui.Button("保存连接", Save, true);
        var cancel = Ui.Button("取消", Close); cancel.DialogResult = DialogResult.Cancel;
        actions.Controls.Add(save); actions.Controls.Add(cancel);
        root.Controls.Add(actions); Controls.Add(root);
        AcceptButton = save; CancelButton = cancel;
        if (connection is not null)
        {
            name.Text = connection.Name; host.Text = connection.Host; port.Value = connection.Port;
            username.Text = connection.Username; group.Text = connection.Group; notes.Text = connection.Notes;
            favorite.Checked = connection.Favorite; password.PlaceholderText = "已保存；留空保持不变";
        }
        UpdateSourceControls();
        FormClosed += (_, _) => password.Clear();
    }

    private void Save()
    {
        try
        {
            string encrypted = password.Text.Length > 0 ? PasswordVault.Protect(password.Text) : original?.ProtectedPassword ?? "";
            Result = (original ?? new Connection()) with
            {
                Name = name.Text.Trim(), Host = host.Text.Trim(), Port = (int)port.Value,
                Username = username.Text.Trim(), ProtectedPassword = encrypted,
                Kind = (ConnectionKind)kind.SelectedIndex, RdpFullScreen = fullScreen.Checked,
                RdpFilePath = kind.SelectedIndex == 1 ? rdpFile.Text : "",
                RdpFileHash = kind.SelectedIndex == 1 ? rdpFileHash : "",
                Group = group.Text.Trim(), Notes = notes.Text.Trim(), Favorite = favorite.Checked
            };
            Result.Validate();
            if (Result.RdpFilePath.Length > 0 && RdpLauncher.Fingerprint(RdpLauncher.ReadProfile(Result.RdpFilePath)) != Result.RdpFileHash)
                throw new IOException("原 RDP 文件已改变，请重新选择文件。");
            if (Result.Kind == ConnectionKind.Ssh)
                _ = Result.MacroConnectCommand(password.Text.Length > 0 ? password.Text : PasswordVault.Unprotect(encrypted));
            DialogResult = DialogResult.OK; Close();
        }
        catch (Exception error) { Ui.Error(this, error); }
    }
}
