using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using LinuxVoiceAssistant.WindowsFront.Audio;
using LinuxVoiceAssistant.WindowsFront.Settings;

namespace LinuxVoiceAssistant.WindowsFront.Protocol;

internal sealed class VapeClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly AudioEngine _audio;
    private readonly SemaphoreSlim _sendGate = new(1, 1);
    private readonly object _settingsGate = new();
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _connectionCts;
    private CancellationTokenSource? _localMuteCts;
    private AppSettings _settings;
    private int _captureSampleRate;
    private bool _captureActive;
    private bool _localMuted;
    private bool _disposed;

    public VapeClient(AppSettings settings, AudioEngine audio)
    {
        _settings = settings.Clone();
        _captureSampleRate = settings.PreferredSampleRate;
        _audio = audio;
    }

    public event Action<AssistantState>? StateChanged;
    public event Action<string>? Error;

    public bool LocalMuted
    {
        get => _localMuted;
        set
        {
            _localMuted = value;
            if (value)
            {
                StopLocalAudio();
                StateChanged?.Invoke(AssistantState.Muted);
                _ = DrainBackendSessionWhileMutedAsync();
            }
            else
            {
                _localMuteCts?.Cancel();
                StateChanged?.Invoke(AssistantState.Idle);
            }
        }
    }

    public void UpdateSettings(AppSettings settings)
    {
        lock (_settingsGate)
        {
            _settings = settings.Clone();
        }
    }

    public async Task WakeOrInterruptAsync()
    {
        if (_localMuted)
        {
            StateChanged?.Invoke(AssistantState.Muted);
            return;
        }

        await EnsureConnectedAsync().ConfigureAwait(false);
        await SendJsonAsync(new
        {
            type = "wake_detected",
            wake_word = "windows_front",
            timestamp_ms = Environment.TickCount64,
        }).ConfigureAwait(false);
    }

    public async Task DisconnectAsync()
    {
        StopLocalAudio();
        var socket = _socket;
        _socket = null;
        _connectionCts?.Cancel();
        _localMuteCts?.Cancel();

        if (socket is not null)
        {
            try
            {
                if (socket.State == WebSocketState.Open)
                {
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "closed", CancellationToken.None).ConfigureAwait(false);
                }
            }
            catch
            {
                // The socket may already be gone; disconnect is best effort.
            }
            finally
            {
                socket.Dispose();
            }
        }

        StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Disconnected);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _connectionCts?.Cancel();
        _localMuteCts?.Cancel();
        _socket?.Dispose();
        _sendGate.Dispose();
    }

    private async Task EnsureConnectedAsync()
    {
        if (_socket?.State == WebSocketState.Open)
        {
            return;
        }

        await DisconnectAsync().ConfigureAwait(false);

        AppSettings settings;
        lock (_settingsGate)
        {
            settings = _settings.Clone();
        }

        var socket = new ClientWebSocket();
        var cts = new CancellationTokenSource();
        await socket.ConnectAsync(settings.ServerUri, cts.Token).ConfigureAwait(false);

        _socket = socket;
        _connectionCts = cts;
        _captureSampleRate = settings.PreferredSampleRate;

        await SendJsonAsync(new
        {
            type = "hello",
            protocol_version = 1,
            device_id = settings.DeviceId,
            firmware_version = Application.ProductVersion,
            capabilities = new
            {
                button = true,
                local_app_mute = true,
            },
            formats = BuildFormats(settings.PreferredSampleRate),
        }).ConfigureAwait(false);

        _ = Task.Run(() => ReceiveLoopAsync(socket, cts.Token));
        StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Idle);
    }

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken cancellationToken)
    {
        var buffer = new byte[64 * 1024];
        var text = new MemoryStream();

        try
        {
            while (!cancellationToken.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                var result = await socket.ReceiveAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    break;
                }

                if (result.MessageType == WebSocketMessageType.Binary)
                {
                    if (!_localMuted)
                    {
                        var copy = new byte[result.Count];
                        Buffer.BlockCopy(buffer, 0, copy, 0, result.Count);
                        _audio.AddPlaybackAudio(copy);
                    }

                    continue;
                }

                text.Write(buffer, 0, result.Count);
                if (!result.EndOfMessage)
                {
                    continue;
                }

                var payload = Encoding.UTF8.GetString(text.ToArray());
                text.SetLength(0);
                HandleControl(payload);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            Error?.Invoke(ex.Message);
        }
        finally
        {
            StopLocalAudio();
            StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Disconnected);
        }
    }

    private void HandleControl(string payload)
    {
        using var document = JsonDocument.Parse(payload);
        var root = document.RootElement;
        if (!root.TryGetProperty("type", out var typeProperty))
        {
            return;
        }

        switch (typeProperty.GetString())
        {
            case "hello_ack":
                if (TryGetSelectedFormat(root, out var sampleRate))
                {
                    _captureSampleRate = sampleRate;
                }
                break;
            case "start_capture":
                StartCapture();
                StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Listening);
                break;
            case "start_playback":
                StartPlayback(root);
                StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Speaking);
                break;
            case "stop_playback":
                _audio.StopPlayback();
                break;
            case "set_state":
                ApplyRemoteState(root);
                break;
            case "session_ended":
            case "stop_capture":
                StopLocalAudio();
                StateChanged?.Invoke(_localMuted ? AssistantState.Muted : AssistantState.Idle);
                break;
            case "error":
                Error?.Invoke(root.TryGetProperty("message", out var message) ? message.GetString() ?? "Server error" : "Server error");
                StateChanged?.Invoke(AssistantState.Error);
                break;
        }
    }

    private void StartCapture()
    {
        if (_localMuted || _captureActive)
        {
            return;
        }

        AppSettings settings;
        lock (_settingsGate)
        {
            settings = _settings.Clone();
        }

        _captureActive = true;
        try
        {
            _audio.StartCapture(settings, _captureSampleRate, chunk => _ = SendAudioAsync(chunk));
        }
        catch (Exception ex)
        {
            _captureActive = false;
            Error?.Invoke(ex.Message);
            StateChanged?.Invoke(AssistantState.Error);
        }
    }

    private void StartPlayback(JsonElement root)
    {
        if (_localMuted)
        {
            return;
        }

        var sampleRate = 24_000;
        if (root.TryGetProperty("format", out var format)
            && format.TryGetProperty("sample_rate", out var sampleRateElement)
            && sampleRateElement.TryGetInt32(out var parsedRate))
        {
            sampleRate = parsedRate;
        }

        AppSettings settings;
        lock (_settingsGate)
        {
            settings = _settings.Clone();
        }

        _audio.StartPlayback(settings, sampleRate);
    }

    private async Task SendAudioAsync(byte[] audio)
    {
        if (_localMuted)
        {
            return;
        }

        var socket = _socket;
        if (socket?.State != WebSocketState.Open)
        {
            return;
        }

        await _sendGate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (socket.State == WebSocketState.Open)
            {
                await socket.SendAsync(audio, WebSocketMessageType.Binary, true, CancellationToken.None).ConfigureAwait(false);
            }
        }
        catch (Exception ex)
        {
            Error?.Invoke(ex.Message);
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private async Task DrainBackendSessionWhileMutedAsync()
    {
        _localMuteCts?.Cancel();
        _localMuteCts = new CancellationTokenSource();
        var cancellationToken = _localMuteCts.Token;

        var socket = _socket;
        if (socket?.State != WebSocketState.Open)
        {
            return;
        }

        try
        {
            await SendJsonAsync(new
            {
                type = "audio_stop",
                timestamp_ms = Environment.TickCount64,
            }).ConfigureAwait(false);

            AppSettings settings;
            lock (_settingsGate)
            {
                settings = _settings.Clone();
            }

            var frameMilliseconds = Math.Max(10, settings.FrameMilliseconds);
            var frameBytes = Math.Max(1, _captureSampleRate * frameMilliseconds / 1000) * 2;
            var silence = new byte[frameBytes];

            while (_localMuted && !cancellationToken.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                await SendBinaryAsync(silence, cancellationToken).ConfigureAwait(false);
                await Task.Delay(frameMilliseconds, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            Error?.Invoke(ex.Message);
        }
    }

    private async Task SendJsonAsync(object message)
    {
        var socket = _socket;
        if (socket?.State != WebSocketState.Open)
        {
            return;
        }

        var json = JsonSerializer.Serialize(message, JsonOptions);
        var bytes = Encoding.UTF8.GetBytes(json);

        await _sendGate.WaitAsync().ConfigureAwait(false);
        try
        {
            await socket.SendAsync(bytes, WebSocketMessageType.Text, true, CancellationToken.None).ConfigureAwait(false);
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private async Task SendBinaryAsync(byte[] audio, CancellationToken cancellationToken)
    {
        var socket = _socket;
        if (socket?.State != WebSocketState.Open)
        {
            return;
        }

        await _sendGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (socket.State == WebSocketState.Open)
            {
                await socket.SendAsync(audio, WebSocketMessageType.Binary, true, cancellationToken).ConfigureAwait(false);
            }
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private void StopLocalAudio()
    {
        _captureActive = false;
        _audio.StopCapture();
        _audio.StopPlayback();
    }

    private void ApplyRemoteState(JsonElement root)
    {
        if (!root.TryGetProperty("state", out var state))
        {
            return;
        }

        var mapped = state.GetString() switch
        {
            "idle" => AssistantState.Idle,
            "listening" => AssistantState.Listening,
            "thinking" => AssistantState.Thinking,
            "speaking" => AssistantState.Speaking,
            "muted" => AssistantState.Muted,
            "error" => AssistantState.Error,
            _ => AssistantState.Idle,
        };

        if (mapped is AssistantState.Idle or AssistantState.Error or AssistantState.Muted)
        {
            StopLocalAudio();
        }

        StateChanged?.Invoke(_localMuted ? AssistantState.Muted : mapped);
    }

    private static bool TryGetSelectedFormat(JsonElement root, out int sampleRate)
    {
        sampleRate = 24_000;
        if (root.TryGetProperty("selected_format", out var format)
            && format.TryGetProperty("sample_rate", out var sampleRateElement)
            && sampleRateElement.TryGetInt32(out var parsedRate))
        {
            sampleRate = parsedRate;
            return true;
        }

        return false;
    }

    private static object[] BuildFormats(int preferredSampleRate)
    {
        var rates = new[] { preferredSampleRate, 24_000, 48_000, 16_000 }.Distinct();
        return rates.Select(rate => new
        {
            codec = "pcm_s16le",
            sample_rate = rate,
            channels = 1,
        }).ToArray();
    }
}
