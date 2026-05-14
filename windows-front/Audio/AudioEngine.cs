using LinuxVoiceAssistant.WindowsFront.Settings;
using NAudio.Wave;

namespace LinuxVoiceAssistant.WindowsFront.Audio;

internal sealed class AudioEngine : IDisposable
{
    private readonly object _gate = new();
    private WaveInEvent? _waveIn;
    private WaveOutEvent? _waveOut;
    private BufferedWaveProvider? _playbackBuffer;
    private bool _disposed;

    public void StartCapture(AppSettings settings, int sampleRate, Action<byte[]> onAudio)
    {
        lock (_gate)
        {
            StopCaptureLocked();

            var waveIn = new WaveInEvent
            {
                DeviceNumber = settings.InputDeviceNumber,
                WaveFormat = new WaveFormat(sampleRate, 16, 1),
                BufferMilliseconds = settings.FrameMilliseconds,
                NumberOfBuffers = 3,
            };

            waveIn.DataAvailable += (_, args) =>
            {
                if (args.BytesRecorded <= 0)
                {
                    return;
                }

                var copy = new byte[args.BytesRecorded];
                Buffer.BlockCopy(args.Buffer, 0, copy, 0, args.BytesRecorded);
                onAudio(copy);
            };

            waveIn.RecordingStopped += (_, _) => StopCapture();
            _waveIn = waveIn;
            _waveIn.StartRecording();
        }
    }

    public void StopCapture()
    {
        lock (_gate)
        {
            StopCaptureLocked();
        }
    }

    public void StartPlayback(AppSettings settings, int sampleRate)
    {
        lock (_gate)
        {
            StopPlaybackLocked();

            _playbackBuffer = new BufferedWaveProvider(new WaveFormat(sampleRate, 16, 1))
            {
                BufferDuration = TimeSpan.FromSeconds(5),
                DiscardOnBufferOverflow = true,
            };

            _waveOut = new WaveOutEvent
            {
                DeviceNumber = settings.OutputDeviceNumber,
                DesiredLatency = 100,
            };
            _waveOut.Init(_playbackBuffer);
            _waveOut.Play();
        }
    }

    public void AddPlaybackAudio(byte[] audio)
    {
        lock (_gate)
        {
            _playbackBuffer?.AddSamples(audio, 0, audio.Length);
        }
    }

    public void StopPlayback()
    {
        lock (_gate)
        {
            StopPlaybackLocked();
        }
    }

    public static IReadOnlyList<DeviceOption> InputDevices()
    {
        var devices = new List<DeviceOption> { DeviceOption.DefaultInput };
        for (var index = 0; index < WaveIn.DeviceCount; index++)
        {
            devices.Add(new DeviceOption(index, WaveIn.GetCapabilities(index).ProductName));
        }

        return devices;
    }

    public static IReadOnlyList<DeviceOption> OutputDevices()
    {
        var devices = new List<DeviceOption> { DeviceOption.DefaultOutput };
        for (var index = 0; index < WaveOut.DeviceCount; index++)
        {
            devices.Add(new DeviceOption(index, WaveOut.GetCapabilities(index).ProductName));
        }

        return devices;
    }

    public void Dispose()
    {
        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }

            StopCaptureLocked();
            StopPlaybackLocked();
            _disposed = true;
        }
    }

    private void StopCaptureLocked()
    {
        var waveIn = _waveIn;
        _waveIn = null;
        if (waveIn is null)
        {
            return;
        }

        try
        {
            waveIn.StopRecording();
        }
        catch
        {
            // Device drivers can throw while being torn down during unplug or app exit.
        }
        finally
        {
            waveIn.Dispose();
        }
    }

    private void StopPlaybackLocked()
    {
        var waveOut = _waveOut;
        _waveOut = null;
        _playbackBuffer = null;
        if (waveOut is null)
        {
            return;
        }

        try
        {
            waveOut.Stop();
        }
        catch
        {
            // See StopCaptureLocked: teardown should be best effort.
        }
        finally
        {
            waveOut.Dispose();
        }
    }
}

internal sealed record DeviceOption(int DeviceNumber, string Name)
{
    public static DeviceOption DefaultInput { get; } = new(-1, "Default microphone");
    public static DeviceOption DefaultOutput { get; } = new(-1, "Default speaker");

    public override string ToString() => Name;
}
