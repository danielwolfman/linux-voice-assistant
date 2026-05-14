using System.Text.Json.Serialization;

namespace LinuxVoiceAssistant.WindowsFront.Settings;

internal sealed class AppSettings
{
    public string ServerUrl { get; set; } = "ws://192.168.1.196:8765/vape";
    public string DeviceId { get; set; } = BuildDefaultDeviceId();
    public int InputDeviceNumber { get; set; } = -1;
    public int OutputDeviceNumber { get; set; } = -1;
    public int PreferredSampleRate { get; set; } = 24_000;
    public int FrameMilliseconds { get; set; } = 20;
    public HotkeySettings Hotkey { get; set; } = HotkeySettings.Default;

    [JsonIgnore]
    public Uri ServerUri => new(ServerUrl);

    public AppSettings Clone()
    {
        return new AppSettings
        {
            ServerUrl = ServerUrl,
            DeviceId = DeviceId,
            InputDeviceNumber = InputDeviceNumber,
            OutputDeviceNumber = OutputDeviceNumber,
            PreferredSampleRate = PreferredSampleRate,
            FrameMilliseconds = FrameMilliseconds,
            Hotkey = Hotkey.Clone(),
        };
    }

    private static string BuildDefaultDeviceId()
    {
        var name = Environment.MachineName.Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(name) ? "windows-front" : $"windows-front-{name}";
    }
}
