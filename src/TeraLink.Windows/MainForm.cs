using TeraLink.Core;

namespace TeraLink.Windows;

internal sealed class MainForm : Form
{
    private readonly ConnectionStore store;
    private AppData data;
    private readonly TextBox search = new() { PlaceholderText = "搜索名称、地址、用户名、分组…", Dock = DockStyle.Fill };
    private readonly CheckBox onlyFavorites = new() { Text = "只看收藏", AutoSize = true };
    private readonly CheckBox exitAfterLaunch = new() { Text = "连接后退出启动器", AutoSize = true };
    private readonly ListView list = new() { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false, BorderStyle = BorderStyle.None };
    private readonly Label title = Ui.Label("连接，只需一步", 23, true);
    private readonly Label description = Ui.Label("保存常用服务器，告别重复输入。", 11);
    private readonly Label details = Ui.Label("点击「新增连接」保存第一台服务器。", 11);
    private readonly Label status = Ui.Label("就绪 · 密码由 Windows 账户加密保存在本机", 9);
    private readonly Label pathLabel = Ui.Label("", 9);
    private readonly Label count = Ui.Label("连接库", 12, true);
    private readonly Button connect;
    private readonly Button edit;
    private readonly Button delete;
    private readonly Button favorite;
    private readonly Button cancel;
    private readonly Button configure;
    private CancellationTokenSource? pending;
    private Task? activeConnection;
    private bool closing;
    private Connection? Selected => list.SelectedItems.Count == 1 ? list.SelectedItems[0].Tag as Connection : null;

    public MainForm(ConnectionStore store, AppData data)
    {
        this.store = store; this.data = data;
        Text = "TeraLink · Tera Term / RDP"; ClientSize = new Size(1080, 720); MinimumSize = new Size(940, 650);
        AutoScaleMode = AutoScaleMode.Dpi; BackColor = Ui.Surface; ForeColor = Ui.Ink;
        StartPosition = FormStartPosition.CenterScreen;

        connect = Ui.Button("连接 →", StartConnection, true);
        edit = Ui.Button("编辑", Edit); delete = Ui.Button("删除", Delete);
        favorite = Ui.Button("收藏", ToggleFavorite);
        cancel = Ui.Button("停止等待", () => pending?.Cancel()); cancel.Visible = false;
        configure = Ui.Button("Tera Term 路径", Configure);

        var root = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 3, Padding = new Padding(28, 24, 28, 14) };
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 90));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 82));

        var header = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2 };
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var brand = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false };
        brand.Controls.Add(Ui.Label("TeraLink", 25, true));
        var subtitle = Ui.Label("你的服务器，随时连接。   /   SSH · RDP", 10); subtitle.ForeColor = Ui.Muted;
        brand.Controls.Add(subtitle); header.Controls.Add(brand, 0, 0);
        var newButton = Ui.Button("＋ 新增连接", () => EditConnection(null), true); newButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        header.Controls.Add(newButton, 1, 0); root.Controls.Add(header, 0, 0);

        var body = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, Margin = new Padding(0) };
        body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 54)); body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 46));
        var library = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.White, Padding = new Padding(18), ColumnCount = 1, RowCount = 4, Margin = new Padding(0, 0, 16, 0) };
        library.RowStyles.Add(new RowStyle(SizeType.Absolute, 38)); library.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
        library.RowStyles.Add(new RowStyle(SizeType.Absolute, 35)); library.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        library.Controls.Add(count); library.Controls.Add(search);
        var options = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
        options.Controls.Add(onlyFavorites); options.Controls.Add(exitAfterLaunch); library.Controls.Add(options);
        list.Columns.Add("连接", 150); list.Columns.Add("类型", 55); list.Columns.Add("主机", 140); list.Columns.Add("分组", 90);
        library.Controls.Add(list); body.Controls.Add(library, 0, 0);

        var card = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.White, Padding = new Padding(24), ColumnCount = 1, RowCount = 5, Margin = new Padding(0) };
        card.RowStyles.Add(new RowStyle(SizeType.AutoSize)); card.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        card.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); card.RowStyles.Add(new RowStyle(SizeType.AutoSize)); card.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        title.MaximumSize = new Size(340, 0); description.MaximumSize = new Size(340, 0); description.ForeColor = Ui.Muted;
        details.MaximumSize = new Size(340, 0); details.Padding = new Padding(0, 20, 0, 0);
        var detailScroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
        detailScroll.Controls.Add(details);
        card.Controls.Add(title); card.Controls.Add(description); card.Controls.Add(detailScroll);
        var primaryActions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(0, 16, 0, 14) };
        primaryActions.Controls.Add(connect); primaryActions.Controls.Add(cancel); card.Controls.Add(primaryActions);
        var secondaryActions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
        secondaryActions.Controls.Add(edit); secondaryActions.Controls.Add(favorite); secondaryActions.Controls.Add(delete); card.Controls.Add(secondaryActions);
        body.Controls.Add(card, 1, 0); root.Controls.Add(body, 0, 1);

        var footer = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, Padding = new Padding(0, 14, 0, 0) };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var footerText = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2 };
        status.AutoSize = false; status.Dock = DockStyle.Fill; status.AutoEllipsis = true; status.Margin = new Padding(0);
        pathLabel.AutoSize = false; pathLabel.Dock = DockStyle.Fill; pathLabel.AutoEllipsis = true; pathLabel.ForeColor = Ui.Muted; pathLabel.Margin = new Padding(0);
        footerText.Controls.Add(status); footerText.Controls.Add(pathLabel); footer.Controls.Add(footerText, 0, 0); footer.Controls.Add(configure, 1, 0);
        root.Controls.Add(footer, 0, 2); Controls.Add(root);

        search.TextChanged += (_, _) => RefreshList(Selected?.Id);
        onlyFavorites.CheckedChanged += (_, _) => RefreshList(Selected?.Id);
        exitAfterLaunch.Checked = data.ExitAfterLaunch;
        exitAfterLaunch.CheckedChanged += (_, _) =>
        {
            if (exitAfterLaunch.Checked == this.data.ExitAfterLaunch) return;
            try { Commit(this.data with { ExitAfterLaunch = exitAfterLaunch.Checked }, Selected?.Id); }
            catch (Exception error) { exitAfterLaunch.Checked = this.data.ExitAfterLaunch; Ui.Error(this, error); }
        };
        list.SelectedIndexChanged += (_, _) => RefreshDetails();
        list.DoubleClick += (_, _) => StartConnection();
        list.KeyDown += (_, args) => { if (args.KeyCode == Keys.Enter) { args.Handled = args.SuppressKeyPress = true; StartConnection(); } };
        KeyPreview = true;
        KeyDown += (_, args) =>
        {
            if (args.Control && args.KeyCode == Keys.N) { args.SuppressKeyPress = true; EditConnection(null); }
            if (args.Control && args.KeyCode == Keys.F) { args.SuppressKeyPress = true; search.Focus(); }
        };
        FormClosing += async (_, args) =>
        {
            if (pending is null) { closing = true; return; }
            args.Cancel = true;
            if (closing) return;
            closing = true; Enabled = false; pending.Cancel();
            // Let the worker stop its macro and clear buffers before the process exits.
            if (activeConnection is not null) await activeConnection;
            Close();
        };
        RefreshList(); RefreshPath();
    }

    private void RefreshPath() => pathLabel.Text = string.IsNullOrEmpty(data.TeraTermPath)
        ? "Tera Term 路径：首次连接时自动检测，也可手动选择。" : $"Tera Term：{data.TeraTermPath}";

    private void RefreshList(Guid? selectedId = null)
    {
        string query = search.Text.Trim();
        var rows = data.Connections.Where(c => (!onlyFavorites.Checked || c.Favorite)
            && $"{c.Name}\n{c.Host}\n{c.Username}\n{c.Group}\n{c.Kind}".Contains(query, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(c => c.Favorite).ThenBy(c => c.Group).ThenBy(c => c.Name).ToArray();
        list.BeginUpdate(); list.Items.Clear();
        foreach (var c in rows)
        {
            var item = new ListViewItem([(c.Favorite ? "★ " : "") + c.Name, c.Kind == ConnectionKind.Rdp ? "RDP" : "SSH", c.Host, c.Group]) { Tag = c };
            list.Items.Add(item); item.Selected = c.Id == selectedId;
        }
        if (list.SelectedItems.Count == 0 && list.Items.Count > 0) list.Items[0].Selected = true;
        list.EndUpdate(); count.Text = $"连接库   {rows.Length} / {data.Connections.Count}";
        RefreshDetails();
    }

    private void RefreshDetails()
    {
        var c = Selected;
        title.Text = c?.Name ?? (data.Connections.Count == 0 ? "连接，只需一步" : "没有匹配的连接");
        description.Text = c is null ? "保存常用服务器，告别重复输入。" : $"{(c.Kind == ConnectionKind.Rdp ? "RDP · Windows 远程桌面" : "SSH · Tera Term")}  ·  {(c.Group.Length == 0 ? "未分组" : c.Group)}";
        details.Text = c is null ? "点击「新增连接」添加服务器，或调整搜索条件。"
            : $"主机地址\n{c.Host}:{c.Port}\n\n登录用户\n{c.Username}\n\n密码\n已加密保存 · Windows 当前账户\n\n上次提交登录\n{(c.LastLaunched?.ToLocalTime().ToString("yyyy-MM-dd HH:mm") ?? "尚未连接")}\n\n备注\n{(c.Notes.Length == 0 ? "—" : c.Notes)}";
        connect.Enabled = c is not null && pending is null;
        edit.Enabled = delete.Enabled = favorite.Enabled = c is not null && pending is null;
        favorite.Text = c?.Favorite == true ? "取消收藏" : "收藏";
        configure.Enabled = pending is null; cancel.Visible = pending is not null && c?.Kind == ConnectionKind.Ssh;
    }

    private void Commit(AppData next, Guid? selected = null)
    {
        store.Save(next); data = next; RefreshList(selected); RefreshPath();
    }

    private void Edit() { if (Selected is { } c) EditConnection(c); }
    private void EditConnection(Connection? c)
    {
        using var dialog = new ConnectionDialog(c);
        if (dialog.ShowDialog(this) != DialogResult.OK || dialog.Result is not { } updated) return;
        try
        {
            var next = data.Connections.Where(item => item.Id != updated.Id).Append(updated).ToList();
            if (c is not null) RdpLauncher.Forget(c.Id);
            Commit(data with { Connections = next }, updated.Id); status.Text = $"已保存「{updated.Name}」。";
        }
        catch (Exception error) { Ui.Error(this, error); }
    }

    private void Delete()
    {
        if (Selected is not { } c) return;
        if (MessageBox.Show(this, $"删除「{c.Name}」及其保存的密码？", "删除连接", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return;
        try { RdpLauncher.Forget(c.Id); Commit(data with { Connections = data.Connections.Where(item => item.Id != c.Id).ToList() }); status.Text = "连接已删除。"; }
        catch (Exception error) { Ui.Error(this, error); }
    }

    private void ToggleFavorite()
    {
        if (Selected is not { } c) return;
        try { Commit(data with { Connections = data.Connections.Select(item => item.Id == c.Id ? item with { Favorite = !item.Favorite } : item).ToList() }, c.Id); }
        catch (Exception error) { Ui.Error(this, error); }
    }

    private void Configure()
    {
        using var dialog = new OpenFileDialog { Title = "选择 Tera Term 的 ttermpro.exe", Filter = "Tera Term (ttermpro.exe)|ttermpro.exe", CheckFileExists = true, Multiselect = false };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        try { TeraTermLauncher.ValidateExecutable(dialog.FileName); Commit(data with { TeraTermPath = dialog.FileName }, Selected?.Id); }
        catch (Exception error) { Ui.Error(this, error); }
    }

    private void StartConnection()
    {
        if (closing || activeConnection?.IsCompleted == false) return;
        activeConnection = ConnectAsync();
    }

    private async Task ConnectAsync()
    {
        if (pending is not null || Selected is not { } connection) return;
        bool launched = false;
        try
        {
            if (connection.Kind == ConnectionKind.Ssh && string.IsNullOrEmpty(data.TeraTermPath))
            {
                if (TeraTermLauncher.FindExecutable() is { } detected)
                    Commit(data with { TeraTermPath = detected }, connection.Id);
                else Configure();
            }
            if (connection.Kind == ConnectionKind.Ssh && string.IsNullOrEmpty(data.TeraTermPath)) return;
            pending = new CancellationTokenSource(); RefreshDetails();
            if (connection.Kind == ConnectionKind.Rdp)
            {
                status.Text = $"正在打开「{connection.Name}」的远程桌面。";
                await Task.Run(() => RdpLauncher.Launch(connection));
            }
            else
            {
                status.Text = $"正在打开「{connection.Name}」；首次连接请在 Tera Term 核对主机指纹。";
                await Task.Run(() => TeraTermLauncher.LaunchAsync(data.TeraTermPath, connection, pending.Token));
            }
            if (closing) return;
            status.Text = connection.Kind == ConnectionKind.Rdp
                ? $"已打开「{connection.Name}」的 RDP 客户端；登录结果请查看远程桌面窗口。"
                : $"「{connection.Name}」宏已完成连接，请在 Tera Term 查看会话。";
            try
            {
                Commit(data with { Connections = data.Connections.Select(c => c.Id == connection.Id ? c with { LastLaunched = DateTimeOffset.Now } : c).ToList() }, connection.Id);
                launched = true;
            }
            catch (Exception error) { status.Text = "已提交登录，但未能保存连接时间。"; Ui.Error(this, error); }
        }
        catch (OperationCanceledException) { if (!closing) status.Text = "已停止等待。登录可能已提交，请在 Tera Term 查看；现有会话保持打开。"; }
        catch (Exception error) { if (!closing) { status.Text = "自动登录未完成。"; Ui.Error(this, error); } }
        finally { pending?.Dispose(); pending = null; if (!closing) RefreshDetails(); }
        if (launched && data.ExitAfterLaunch && !closing) Close();
    }
}
