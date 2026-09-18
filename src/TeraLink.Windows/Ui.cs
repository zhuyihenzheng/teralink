namespace TeraLink.Windows;

internal static class Ui
{
    public static readonly Color Ink = Color.FromArgb(29, 45, 52);
    public static readonly Color Muted = Color.FromArgb(92, 109, 117);
    public static readonly Color Accent = Color.FromArgb(0, 112, 100);
    public static readonly Color Surface = Color.FromArgb(245, 248, 248);

    public static Button Button(string text, Action action, bool primary = false)
    {
        var button = new Button
        {
            Text = text, AutoSize = true, MinimumSize = new Size(92, 38), Padding = new Padding(10, 4, 10, 4),
            FlatStyle = FlatStyle.Flat, BackColor = primary ? Accent : Color.White,
            ForeColor = primary ? Color.White : Ink, Cursor = Cursors.Hand, Margin = new Padding(0, 0, 10, 0)
        };
        button.FlatAppearance.BorderColor = primary ? Accent : Color.FromArgb(210, 221, 224);
        button.Click += (_, _) => action();
        return button;
    }

    public static Label Label(string text, float size = 10, bool bold = false) => new()
    {
        Text = text, AutoSize = true, ForeColor = Ink, Margin = new Padding(0, 0, 0, 10),
        Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular)
    };

    public static void Error(IWin32Window owner, Exception error) => MessageBox.Show(owner, error.Message, "操作未完成", MessageBoxButtons.OK, MessageBoxIcon.Warning);
}
