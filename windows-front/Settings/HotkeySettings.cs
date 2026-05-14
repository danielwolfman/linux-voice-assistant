namespace LinuxVoiceAssistant.WindowsFront.Settings;

internal sealed class HotkeySettings
{
    public static HotkeySettings Default => new()
    {
        Key = Keys.Space,
        Control = true,
        Alt = true,
    };

    public Keys Key { get; set; } = Keys.Space;
    public bool Control { get; set; }
    public bool Alt { get; set; }
    public bool Shift { get; set; }
    public bool Windows { get; set; }

    public HotkeySettings Clone()
    {
        return new HotkeySettings
        {
            Key = Key,
            Control = Control,
            Alt = Alt,
            Shift = Shift,
            Windows = Windows,
        };
    }

    public override string ToString()
    {
        var parts = new List<string>();
        if (Control)
        {
            parts.Add("Ctrl");
        }
        if (Alt)
        {
            parts.Add("Alt");
        }
        if (Shift)
        {
            parts.Add("Shift");
        }
        if (Windows)
        {
            parts.Add("Win");
        }

        parts.Add(Key.ToString());
        return string.Join("+", parts);
    }
}
