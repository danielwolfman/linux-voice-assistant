"""Discord bridge for Codex jobs and assistant message delivery."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from .codex_agent import CodexJob, CodexJobManager

_LOGGER = logging.getLogger(__name__)

DISCORD_ORIGIN_PREFIX = "discord:"
_DISCORD_ID_RE = re.compile(r"\d{15,25}")
_STILL_WORKING_SECONDS = 300
_MAX_REPLY_CONTEXT_MESSAGES = 12
_MAX_REPLY_CONTEXT_CHARS = 6000


@dataclass(frozen=True)
class DiscordSendResult:
    user_id: str
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"user_id": self.user_id, "status": self.status, "error": self.error}


@dataclass
class DiscordJobContext:
    user_id: str
    channel: Any
    message: Any
    still_working_task: Optional[asyncio.Task[None]] = None


@dataclass(frozen=True)
class DiscordContextMessage:
    author: str
    content: str
    is_bot: bool = False


def parse_discord_user_ids(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = " ".join(str(item) for item in value)
    else:
        raw = str(value)
    return list(dict.fromkeys(_DISCORD_ID_RE.findall(raw)))


def discord_origin_session_id(user_id: str) -> str:
    return f"{DISCORD_ORIGIN_PREFIX}{user_id}"


def discord_user_id_from_origin(origin_session_id: Optional[str]) -> str:
    if not origin_session_id or not origin_session_id.startswith(DISCORD_ORIGIN_PREFIX):
        return ""
    return origin_session_id[len(DISCORD_ORIGIN_PREFIX) :].strip()


class DiscordBotService:
    def __init__(
        self,
        *,
        token: str,
        client_id: str,
        allowed_user_ids: object,
        codex_manager: CodexJobManager,
    ) -> None:
        self._token = token.strip()
        self._client_id = client_id.strip()
        self._allowed_user_ids = set(parse_discord_user_ids(allowed_user_ids))
        self._codex_manager = codex_manager
        self._client: Any = None
        self._task: Optional[asyncio.Task[Any]] = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._job_contexts: dict[str, DiscordJobContext] = {}

    @property
    def configured(self) -> bool:
        return bool(self._token)

    @property
    def allowed_user_ids(self) -> list[str]:
        return sorted(self._allowed_user_ids)

    def set_allowed_user_ids(self, value: object) -> None:
        self._allowed_user_ids = set(parse_discord_user_ids(value))
        _LOGGER.info("Updated Discord allowlist: %s", ", ".join(sorted(self._allowed_user_ids)) or "empty")

    async def start(self) -> None:
        if not self._token:
            _LOGGER.info("Discord bot bridge disabled because no bot token is configured")
            return
        if self._task is not None:
            return

        try:
            import discord  # type: ignore[import-not-found]
        except ImportError:
            _LOGGER.exception("discord.py is required for the Discord bot bridge")
            return

        intents = discord.Intents.default()
        intents.dm_messages = True
        intents.guild_messages = True
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready() -> None:
            user = getattr(self._client, "user", None)
            if user is not None and not self._client_id:
                self._client_id = str(getattr(user, "id", "") or "")
            _LOGGER.info("Discord bot bridge connected as %s", user)
            self._ready.set()

        @self._client.event
        async def on_message(message: Any) -> None:
            await self._handle_message(message)

        self._task = asyncio.create_task(self._client.start(self._token))
        self._task.add_done_callback(self._log_task_result)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        for context in self._job_contexts.values():
            if context.still_working_task is not None:
                context.still_working_task.cancel()
        self._job_contexts.clear()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.exception("Discord bot bridge stopped with an error")
            self._task = None

    def _log_task_result(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Discord bot bridge stopped: %s", err)

    async def send_message(self, message: str, user_ids: object = None) -> dict[str, Any]:
        recipients = parse_discord_user_ids(user_ids) if user_ids else self.allowed_user_ids
        if not recipients:
            return {"status": "error", "error": "No Discord recipients are configured."}
        if not self._client or not self._ready.is_set():
            return {"status": "error", "error": "Discord bot is not connected."}

        results = []
        for user_id in recipients:
            if user_id not in self._allowed_user_ids:
                results.append(DiscordSendResult(user_id=user_id, status="denied", error="User is not in the Discord allowlist"))
                continue
            results.append(await self._send_dm(user_id, message))
        ok_count = sum(1 for result in results if result.status == "sent")
        return {
            "status": "sent" if ok_count else "error",
            "sent": ok_count,
            "results": [result.as_dict() for result in results],
        }

    async def notify_codex_job_finished(self, job: CodexJob) -> None:
        user_id = discord_user_id_from_origin(job.origin_session_id)
        if not user_id:
            return
        if job.status == "succeeded":
            message = job.final_output.strip() or "Codex finished without a final message."
        else:
            message = job.error or job.final_output or job.last_event or "No details were reported."

        context = self._job_contexts.pop(job.id, None)
        if context is not None:
            if context.still_working_task is not None:
                context.still_working_task.cancel()
            await self._reply_to_context(context, message)
            return

        result = await self.send_message(message, [user_id])
        if result.get("status") != "sent":
            _LOGGER.warning("Failed to send Discord Codex completion for job %s: %s", job.id, result)

    async def _handle_message(self, message: Any) -> None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return

        author_id = str(getattr(author, "id", "") or "")
        if author_id not in self._allowed_user_ids:
            return

        raw_content = str(getattr(message, "content", "") or "").strip()
        if not raw_content:
            return

        if not _is_dm(message) and not self._mentions_bot(message) and not self._is_reply_to_bot(message):
            return

        content = self._strip_bot_mention(raw_content).strip()
        if not content:
            return

        context_messages = await self._reply_context_messages(message)
        async with self._lock:
            result = await self._execute_discord_command(author_id, content, context_messages)
        reply = result.get("reply")
        if isinstance(reply, str) and reply:
            await message.channel.send(reply)
            return

        start_result = result.get("start_result")
        if not isinstance(start_result, dict):
            return
        if start_result.get("status") != "accepted":
            await message.channel.send(_format_start_result(start_result))
            return

        job = start_result.get("job") or {}
        job_id = str(job.get("id") or "")
        if not job_id:
            return
        await self._react_accepted(message)
        self._remember_job_context(job_id, author_id, message)

    async def _execute_discord_command(self, author_id: str, content: str, context_messages: list[DiscordContextMessage] | None = None) -> dict[str, Any]:
        command = content.strip()
        command_key = command.lower()
        if command_key in {"help", "/help"}:
            return {"reply": "Send a Codex task here, or use `status` / `cancel`."}
        if command_key in {"status", "/status"}:
            return {"reply": _format_status_result(self._codex_manager.get_status(""))}
        if command_key in {"cancel", "/cancel"}:
            return {"reply": _format_status_result(await self._codex_manager.cancel_task(""))}

        task = _build_codex_task(command, context_messages or [])
        result = await self._codex_manager.start_task(
            {"task": task, "execution_mode": "docker"},
            origin_session_id=discord_origin_session_id(author_id),
            allow_parallel=True,
        )
        return {"start_result": result}

    def _remember_job_context(self, job_id: str, author_id: str, message: Any) -> None:
        context = DiscordJobContext(user_id=author_id, channel=message.channel, message=message)
        context.still_working_task = asyncio.create_task(self._send_still_working_if_active(job_id))
        self._job_contexts[job_id] = context

    async def _send_still_working_if_active(self, job_id: str) -> None:
        try:
            await asyncio.sleep(_STILL_WORKING_SECONDS)
            context = self._job_contexts.get(job_id)
            if context is None:
                return
            status = self._codex_manager.get_status(job_id)
            job = status.get("job") or {}
            if isinstance(job, dict) and job.get("status") in {"queued", "running", "cancelling"}:
                await self._reply_to_context(context, "I'm still working on it")
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to send Discord still-working update for Codex job %s", job_id)

    async def _reply_to_context(self, context: DiscordJobContext, message: str) -> None:
        text = _truncate_discord_message(message)
        try:
            await context.channel.send(text, reference=context.message)
        except TypeError:
            await context.channel.send(text)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to reply to Discord Codex job context; falling back to DM")
            await self.send_message(text, [context.user_id])

    async def _react_accepted(self, message: Any) -> None:
        try:
            await message.add_reaction("\N{EYES}")
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to react to accepted Discord Codex task")

    async def _send_dm(self, user_id: str, message: str) -> DiscordSendResult:
        try:
            user = self._client.get_user(int(user_id))
            if user is None:
                user = await self._client.fetch_user(int(user_id))
            await user.send(_truncate_discord_message(message))
            return DiscordSendResult(user_id=user_id, status="sent")
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to send Discord DM to %s", user_id)
            return DiscordSendResult(user_id=user_id, status="error", error=str(err))

    def _mentions_bot(self, message: Any) -> bool:
        client_user = getattr(self._client, "user", None)
        bot_id = str(getattr(client_user, "id", "") or self._client_id)
        if not bot_id:
            return False
        if f"<@{bot_id}>" in str(getattr(message, "content", "")) or f"<@!{bot_id}>" in str(getattr(message, "content", "")):
            return True
        return any(str(getattr(mention, "id", "") or "") == bot_id for mention in getattr(message, "mentions", []) or [])

    def _is_reply_to_bot(self, message: Any) -> bool:
        referenced = _resolved_reference_message(message)
        return referenced is not None and self._is_bot_message(referenced)

    def _is_bot_message(self, message: Any) -> bool:
        author = getattr(message, "author", None)
        if author is None:
            return False
        client_user = getattr(self._client, "user", None)
        bot_id = str(getattr(client_user, "id", "") or self._client_id)
        author_id = str(getattr(author, "id", "") or "")
        return bool(bot_id and author_id == bot_id) or bool(getattr(author, "bot", False) and (not bot_id or author_id == bot_id))

    async def _reply_context_messages(self, message: Any) -> list[DiscordContextMessage]:
        chain: list[Any] = []
        current = message
        seen_ids: set[str] = set()
        for _ in range(_MAX_REPLY_CONTEXT_MESSAGES + 1):
            message_id = str(getattr(current, "id", "") or id(current))
            if message_id in seen_ids:
                break
            seen_ids.add(message_id)
            chain.append(current)
            referenced = await self._fetch_referenced_message(current)
            if referenced is None:
                break
            current = referenced

        previous_messages = list(reversed(chain))[0:-1]
        context_messages: list[DiscordContextMessage] = []
        for item in previous_messages[-_MAX_REPLY_CONTEXT_MESSAGES:]:
            context = self._context_message_from_discord_message(item)
            if context is not None:
                context_messages.append(context)
        return context_messages

    async def _fetch_referenced_message(self, message: Any) -> Any | None:
        referenced = _resolved_reference_message(message)
        if referenced is not None:
            return referenced
        reference = getattr(message, "reference", None)
        message_id = getattr(reference, "message_id", None)
        channel = getattr(message, "channel", None)
        fetch_message = getattr(channel, "fetch_message", None)
        if message_id is None or not callable(fetch_message):
            return None
        try:
            return await fetch_message(message_id)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Failed to fetch referenced Discord message %s", message_id, exc_info=True)
            return None

    def _context_message_from_discord_message(self, message: Any) -> DiscordContextMessage | None:
        author = getattr(message, "author", None)
        if author is None:
            return None
        author_id = str(getattr(author, "id", "") or "")
        is_bot = self._is_bot_message(message)
        if not is_bot and author_id not in self._allowed_user_ids:
            return None

        content = self._strip_bot_mention(str(getattr(message, "content", "") or "")).strip()
        if not content:
            return None
        return DiscordContextMessage(
            author=_display_author(author, is_bot=is_bot),
            content=content,
            is_bot=is_bot,
        )

    def _strip_bot_mention(self, content: str) -> str:
        if not self._client_id:
            return content
        return content.replace(f"<@{self._client_id}>", "").replace(f"<@!{self._client_id}>", "").strip()


class DiscordTool:
    def __init__(self, service: DiscordBotService) -> None:
        self._service = service

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "send_discord_message",
                "description": "Send text or links to the configured Discord allowlist by DM. Use when the user asks you to send them something in Discord.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The complete message to send. Include links exactly as they should appear.",
                        },
                        "user_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional subset of allowed Discord user ids. Omit to send to every allowed user.",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            }
        ]

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "send_discord_message":
            raise ValueError(f"Unsupported Discord tool: {name}")
        message = str(arguments.get("message") or "").strip()
        if not message:
            return {"status": "error", "error": "A non-empty message is required."}
        return await self._service.send_message(message, arguments.get("user_ids"))

    async def close(self) -> None:
        return None


def _is_dm(message: Any) -> bool:
    guild = getattr(message, "guild", None)
    return guild is None


def _resolved_reference_message(message: Any) -> Any | None:
    reference = getattr(message, "reference", None)
    if reference is None:
        return None
    resolved = getattr(reference, "resolved", None)
    return resolved if resolved is not None else None


def _display_author(author: Any, *, is_bot: bool) -> str:
    if is_bot:
        return "Mycroft"
    for attr in ("display_name", "global_name", "name"):
        value = str(getattr(author, attr, "") or "").strip()
        if value:
            return value
    return str(getattr(author, "id", "") or "user")


def _build_codex_task(command: str, context_messages: list[DiscordContextMessage]) -> str:
    if not context_messages:
        return command
    context = _format_reply_context(context_messages)
    if not context:
        return command
    return (
        "The user sent this Discord task as part of a reply chain. Use the previous Discord replies as context, "
        "but treat the current Discord message as the task to perform.\n\n"
        "Previous Discord replies, oldest to newest:\n"
        f"{context}\n\n"
        "Current Discord task:\n"
        f"{command}"
    )


def _format_reply_context(context_messages: list[DiscordContextMessage]) -> str:
    lines = []
    remaining = _MAX_REPLY_CONTEXT_CHARS
    for message in context_messages:
        line = f"{message.author}: {message.content}"
        if len(line) > remaining:
            lines.append(line[: max(0, remaining - 24)].rstrip() + "\n[context truncated]")
            break
        lines.append(line)
        remaining -= len(line) + 1
        if remaining <= 0:
            break
    return "\n".join(lines).strip()


def _format_start_result(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "accepted":
        return ""
    if status == "busy":
        active = result.get("active_job") or {}
        return f"Codex is already running job {active.get('id', '')}. Latest: {active.get('last_event') or 'starting'}"
    return str(result.get("message") or result.get("error") or result)


def _format_status_result(result: dict[str, Any]) -> str:
    status = result.get("status")
    job = result.get("job") or result.get("active_job") or {}
    if isinstance(job, dict) and job:
        latest = job.get("last_event") or job.get("error") or "No events yet."
        return f"Codex job {job.get('id', '')} is {job.get('status', status)}. Latest: {_truncate_discord_message(str(latest), limit=1500)}"
    return str(result.get("message") or result.get("error") or result)


def _truncate_discord_message(message: str, *, limit: int = 1900) -> str:
    stripped = message.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 30].rstrip() + "\n[message truncated]"
