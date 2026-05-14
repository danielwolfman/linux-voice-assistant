namespace LinuxVoiceAssistant.WindowsFront;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new Tray.TrayApplicationContext(new Settings.ConfigStore()));
    }
}
