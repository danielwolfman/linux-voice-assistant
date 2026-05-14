using System.Media;
using LinuxVoiceAssistant.WindowsFront.Audio;
using LinuxVoiceAssistant.WindowsFront.Hotkeys;
using LinuxVoiceAssistant.WindowsFront.Protocol;
using LinuxVoiceAssistant.WindowsFront.Settings;

namespace LinuxVoiceAssistant.WindowsFront.Tray;

internal sealed class TrayApplicationContext : ApplicationContext
{
    private readonly ConfigStore _configStore;
    private readonly AudioEngine _audio = new();
    private readonly NotifyIcon _notifyIcon;
    private readonly ToolStripMenuItem _stateItem;
    private readonly ToolStripMenuItem _wakeItem;
    private readonly ToolStripMenuItem _muteItem;
    private readonly ToolStripMenuItem _settingsItem;
    private readonly ToolStripMenuItem _exitItem;
    private readonly GlobalHotkey _hotkey = new();
    private readonly SynchronizationContext _uiContext;
    private readonly VapeClient _client;
    private AppSettings _settings;
    private AssistantState _state = AssistantState.Disconnected;

    public TrayApplicationContext(ConfigStore configStore)
    {
        _uiContext = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        _configStore = configStore;
        _settings = _configStore.Load();
        _client = new VapeClient(_settings, _audio);
        _client.StateChanged += OnStateChanged;
        _client.Error += OnError;

        _stateItem = new ToolStripMenuItem("Disconnected") { Enabled = false };
        _wakeItem = new ToolStripMenuItem("Wake / Interrupt", null, (_, _) => _ = WakeOrInterruptAsync());
        _muteItem = new ToolStripMenuItem("Muted", null, (_, _) => ToggleMute()) { CheckOnClick = true };
        _settingsItem = new ToolStripMenuItem("Settings", null, (_, _) => ShowSettings());
        _exitItem = new ToolStripMenuItem("Exit", null, async (_, _) => await ExitAsync());

        _notifyIcon = new NotifyIcon
        {
            Icon = SystemIcons.Application,
            Text = "Windows Voice Front",
            Visible = true,
            ContextMenuStrip = new ContextMenuStrip
            {
                Items =
                {
                    _stateItem,
                    new ToolStripSeparator(),
                    _wakeItem,
                    _muteItem,
                    _settingsItem,
                    new ToolStripSeparator(),
                    _exitItem,
                },
            },
        };
        _notifyIcon.DoubleClick += (_, _) => _ = WakeOrInterruptAsync();

        RegisterHotkey();
        UpdateState(AssistantState.Disconnected);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _notifyIcon.Visible = false;
            _notifyIcon.Dispose();
            _hotkey.Dispose();
            _client.Dispose();
            _audio.Dispose();
        }

        base.Dispose(disposing);
    }

    private async Task WakeOrInterruptAsync()
    {
        if (_client.LocalMuted)
        {
            UpdateState(AssistantState.Muted);
            return;
        }

        try
        {
            await _client.WakeOrInterruptAsync();
            PlayWakeSound();
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
    }

    private void ToggleMute()
    {
        _client.LocalMuted = _muteItem.Checked;
        UpdateState(_muteItem.Checked ? AssistantState.Muted : AssistantState.Idle);
    }

    private void ShowSettings()
    {
        using var form = new SettingsForm(_settings);
        if (form.ShowDialog() != DialogResult.OK)
        {
            return;
        }

        _settings = form.Settings.Clone();
        _configStore.Save(_settings);
        _client.UpdateSettings(_settings);
        RegisterHotkey();
    }

    private void RegisterHotkey()
    {
        try
        {
            _hotkey.Register(_settings.Hotkey, () => _ = WakeOrInterruptAsync());
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
    }

    private void OnStateChanged(AssistantState state)
    {
        _uiContext.Post(_ => UpdateState(state), null);
    }

    private void OnError(string message)
    {
        _uiContext.Post(_ => ShowError(message), null);
    }

    private void UpdateState(AssistantState state)
    {
        var previousState = _state;
        _state = state;
        _stateItem.Text = StateLabel(state);
        _notifyIcon.Text = $"Windows Voice Front - {_stateItem.Text}";
        _wakeItem.Enabled = state != AssistantState.Muted;
        _muteItem.Checked = state == AssistantState.Muted || _client.LocalMuted;

        if (IsActiveState(previousState) && IsDownState(state))
        {
            PlayWakeDownSound();
        }
    }

    private void ShowError(string message)
    {
        UpdateState(AssistantState.Error);
        _notifyIcon.ShowBalloonTip(5_000, "Windows Voice Front", message, ToolTipIcon.Error);
    }

    private async Task ExitAsync()
    {
        await _client.DisconnectAsync();
        ExitThread();
    }

    private static string StateLabel(AssistantState state)
    {
        return state switch
        {
            AssistantState.Disconnected => "Disconnected",
            AssistantState.Idle => "Idle",
            AssistantState.Listening => "Listening",
            AssistantState.Thinking => "Thinking",
            AssistantState.Speaking => "Speaking",
            AssistantState.Muted => "Muted",
            AssistantState.Error => "Error",
            _ => "Unknown",
        };
    }

    private static bool IsActiveState(AssistantState state)
    {
        return state is AssistantState.Listening or AssistantState.Thinking or AssistantState.Speaking;
    }

    private static bool IsDownState(AssistantState state)
    {
        return state is AssistantState.Idle or AssistantState.Muted or AssistantState.Disconnected or AssistantState.Error;
    }

    private static void PlayWakeSound()
    {
        try
        {
            SystemSounds.Asterisk.Play();
        }
        catch
        {
            // System sound playback is best effort and should never block wake.
        }
    }

    private static void PlayWakeDownSound()
    {
        try
        {
            SystemSounds.Exclamation.Play();
        }
        catch
        {
            // System sound playback is best effort and should never affect state.
        }
    }
}
