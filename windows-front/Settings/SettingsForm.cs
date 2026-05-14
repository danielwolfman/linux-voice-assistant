using LinuxVoiceAssistant.WindowsFront.Audio;

namespace LinuxVoiceAssistant.WindowsFront.Settings;

internal sealed class SettingsForm : Form
{
    private readonly TextBox _serverUrl = new();
    private readonly TextBox _deviceId = new();
    private readonly ComboBox _inputDevice = new();
    private readonly ComboBox _outputDevice = new();
    private readonly ComboBox _sampleRate = new();
    private readonly NumericUpDown _frameMs = new();
    private readonly TextBox _hotkey = new();
    private HotkeySettings _selectedHotkey;

    public SettingsForm(AppSettings settings)
    {
        Settings = settings.Clone();
        _selectedHotkey = Settings.Hotkey.Clone();

        Text = "Windows Voice Front Settings";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        Width = 520;
        Height = 360;

        var table = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(12),
            ColumnCount = 2,
            RowCount = 8,
        };
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 130));
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

        AddRow(table, 0, "Server", _serverUrl);
        AddRow(table, 1, "Device ID", _deviceId);
        AddRow(table, 2, "Microphone", _inputDevice);
        AddRow(table, 3, "Speaker", _outputDevice);
        AddRow(table, 4, "Sample rate", _sampleRate);
        AddRow(table, 5, "Frame ms", _frameMs);
        AddRow(table, 6, "Hotkey", _hotkey);

        _serverUrl.Text = Settings.ServerUrl;
        _deviceId.Text = Settings.DeviceId;
        ConfigureDeviceCombo(_inputDevice, AudioEngine.InputDevices(), Settings.InputDeviceNumber);
        ConfigureDeviceCombo(_outputDevice, AudioEngine.OutputDevices(), Settings.OutputDeviceNumber);
        _sampleRate.DropDownStyle = ComboBoxStyle.DropDownList;
        _sampleRate.Items.AddRange(new object[] { 24_000, 48_000, 16_000 });
        _sampleRate.SelectedItem = Settings.PreferredSampleRate;
        if (_sampleRate.SelectedIndex < 0)
        {
            _sampleRate.SelectedIndex = 0;
        }

        _frameMs.Minimum = 10;
        _frameMs.Maximum = 100;
        _frameMs.Increment = 10;
        _frameMs.Value = Math.Clamp(Settings.FrameMilliseconds, 10, 100);

        _hotkey.ReadOnly = true;
        _hotkey.Text = _selectedHotkey.ToString();
        _hotkey.KeyDown += HotkeyOnKeyDown;

        var buttons = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.RightToLeft,
        };
        var save = new Button { Text = "Save", DialogResult = DialogResult.OK, Width = 90 };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, Width = 90 };
        buttons.Controls.Add(save);
        buttons.Controls.Add(cancel);
        table.Controls.Add(buttons, 0, 7);
        table.SetColumnSpan(buttons, 2);

        AcceptButton = save;
        CancelButton = cancel;
        Controls.Add(table);
    }

    public AppSettings Settings { get; private set; }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (DialogResult == DialogResult.OK)
        {
            if (!Uri.TryCreate(_serverUrl.Text.Trim(), UriKind.Absolute, out var uri)
                || (uri.Scheme != "ws" && uri.Scheme != "wss"))
            {
                MessageBox.Show(this, "Server must be a ws:// or wss:// URL.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                e.Cancel = true;
                return;
            }

            Settings.ServerUrl = _serverUrl.Text.Trim();
            Settings.DeviceId = string.IsNullOrWhiteSpace(_deviceId.Text) ? Settings.DeviceId : _deviceId.Text.Trim();
            Settings.InputDeviceNumber = ((DeviceOption)_inputDevice.SelectedItem!).DeviceNumber;
            Settings.OutputDeviceNumber = ((DeviceOption)_outputDevice.SelectedItem!).DeviceNumber;
            Settings.PreferredSampleRate = (int)_sampleRate.SelectedItem!;
            Settings.FrameMilliseconds = (int)_frameMs.Value;
            Settings.Hotkey = _selectedHotkey.Clone();
        }

        base.OnFormClosing(e);
    }

    private static void AddRow(TableLayoutPanel table, int row, string label, Control control)
    {
        table.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        table.Controls.Add(new Label
        {
            Text = label,
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleLeft,
        }, 0, row);
        control.Dock = DockStyle.Fill;
        table.Controls.Add(control, 1, row);
    }

    private static void ConfigureDeviceCombo(ComboBox combo, IReadOnlyList<DeviceOption> devices, int selectedDevice)
    {
        combo.DropDownStyle = ComboBoxStyle.DropDownList;
        foreach (var device in devices)
        {
            combo.Items.Add(device);
            if (device.DeviceNumber == selectedDevice)
            {
                combo.SelectedItem = device;
            }
        }

        if (combo.SelectedIndex < 0)
        {
            combo.SelectedIndex = 0;
        }
    }

    private void HotkeyOnKeyDown(object? sender, KeyEventArgs e)
    {
        var key = e.KeyCode;
        if (key is Keys.ControlKey or Keys.Menu or Keys.ShiftKey or Keys.LWin or Keys.RWin)
        {
            return;
        }

        _selectedHotkey = new HotkeySettings
        {
            Key = key,
            Control = e.Control,
            Alt = e.Alt,
            Shift = e.Shift,
            Windows = (e.Modifiers & Keys.LWin) == Keys.LWin || (e.Modifiers & Keys.RWin) == Keys.RWin,
        };
        _hotkey.Text = _selectedHotkey.ToString();
        e.SuppressKeyPress = true;
    }
}
