using TeraLink.Core;

namespace TeraLink.Windows;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.SetDefaultFont(new Font("Microsoft YaHei UI", 10));
        string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TeraLink");
        try
        {
            using var store = new ConnectionStore(directory);
            var data = store.Load();
            Application.Run(new MainForm(store, data));
        }
        catch (Exception error)
        {
            MessageBox.Show($"无法打开 TeraLink。\n\n{error.Message}\n\n若已有窗口，请使用已打开的窗口。连接文件不会被重置。\n数据目录：{directory}", "TeraLink", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
