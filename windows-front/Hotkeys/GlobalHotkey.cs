using System.ComponentModel;
using System.Runtime.InteropServices;
using LinuxVoiceAssistant.WindowsFront.Settings;

namespace LinuxVoiceAssistant.WindowsFront.Hotkeys;

internal sealed class GlobalHotkey : NativeWindow, IDisposable
{
    private const int HotkeyId = 0x4C5641;
    private const int WmHotkey = 0x0312;
    private bool _registered;
    private bool _disposed;
    private Action? _callback;

    public void Register(HotkeySettings hotkey, Action callback)
    {
        Unregister();
        _callback = callback;
        CreateHandle(new CreateParams());

        var modifiers = Modifiers.None;
        if (hotkey.Control)
        {
            modifiers |= Modifiers.Control;
        }
        if (hotkey.Alt)
        {
            modifiers |= Modifiers.Alt;
        }
        if (hotkey.Shift)
        {
            modifiers |= Modifiers.Shift;
        }
        if (hotkey.Windows)
        {
            modifiers |= Modifiers.Windows;
        }

        _registered = RegisterHotKey(Handle, HotkeyId, (uint)modifiers, (uint)hotkey.Key);
        if (!_registered)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), $"Could not register hotkey {hotkey}");
        }
    }

    public void Unregister()
    {
        if (_registered)
        {
            UnregisterHotKey(Handle, HotkeyId);
            _registered = false;
        }

        if (Handle != IntPtr.Zero)
        {
            DestroyHandle();
        }
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == WmHotkey && message.WParam.ToInt32() == HotkeyId)
        {
            _callback?.Invoke();
            return;
        }

        base.WndProc(ref message);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        Unregister();
        _disposed = true;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    [Flags]
    private enum Modifiers : uint
    {
        None = 0,
        Alt = 0x0001,
        Control = 0x0002,
        Shift = 0x0004,
        Windows = 0x0008,
    }
}
