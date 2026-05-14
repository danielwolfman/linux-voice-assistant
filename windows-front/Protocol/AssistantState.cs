namespace LinuxVoiceAssistant.WindowsFront.Protocol;

internal enum AssistantState
{
    Disconnected,
    Idle,
    Listening,
    Thinking,
    Speaking,
    Muted,
    Error,
}
