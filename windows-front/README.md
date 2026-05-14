# Windows Voice Front

Windows Voice Front is a small .NET tray client for the Linux voice assistant VAPE WebSocket frontend.

It keeps the assistant brain on the Linux host and acts as a Windows microphone, speaker, wake button, hotkey, and local-only mute surface.

Default backend URL:

```text
ws://192.168.1.197:8765/vape
```

## Scope

- global hotkey wake/interruption
- tray double-click wake/interruption
- local-only app mute
- configurable microphone, speaker, sample rate, frame size, and hotkey
- PCM16 mono microphone uplink while the backend is listening
- PCM16 mono assistant playback downlink

Mute is deliberately local-only. When muted, the Windows app blocks wake, real microphone capture, and playback locally without changing Linux backend mute state. If mute is pressed mid-session, the app sends the existing `audio_stop` control frame and silent PCM frames so the current backend can return to idle through its normal timeout path.

## Backend

Run the Linux assistant in VAPE server mode:

```sh
python -m linux_voice_assistant \
  --config local/realtime-home-assistant.yaml \
  --frontend vape-server \
  --vape-server-host 0.0.0.0 \
  --vape-server-port 8765 \
  --vape-server-path /vape
```

## Build

Install the .NET 8 SDK on Windows, then:

```powershell
cd windows-front
dotnet restore
dotnet run -c Release
```

Publish a framework-dependent Windows build:

```powershell
dotnet publish -c Release -r win-x64 --self-contained false
```

## Protocol Notes

The app uses the existing VAPE-style protocol:

1. Send `hello` with PCM16 mono formats.
2. Send `wake_detected` for hotkey or tray wake.
3. Start microphone capture when Linux sends `start_capture`.
4. Continue sending microphone PCM, including silence, while the backend is listening.
5. Start speaker playback when Linux sends `start_playback`.
6. Stop local playback on `stop_playback`.

The Linux backend owns VAD and turn commit timing, so the Windows app does not stop capture when local silence is detected.
