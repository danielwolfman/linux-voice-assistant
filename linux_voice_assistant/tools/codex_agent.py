"""Asynchronous Codex CLI job runner exposed as Realtime tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

_LOGGER = logging.getLogger(__name__)

CodexCompletionCallback = Callable[["CodexJob"], Awaitable[None]]


@dataclass
class CodexJob:
    id: str
    task: str
    workspace: Path
    execution_mode: str
    origin_session_id: Optional[str]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    return_code: Optional[int] = None
    final_output: str = ""
    last_event: str = ""
    error: str = ""
    job_dir: Path = Path()
    final_output_path: Path = Path()
    events_path: Path = Path()
    stderr_path: Path = Path()
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False, compare=False)

    @property
    def is_active(self) -> bool:
        return self.status in {"queued", "running", "cancelling"}

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "workspace": os.fspath(self.workspace),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(_elapsed_seconds(self), 1),
            "return_code": self.return_code,
            "last_event": self.last_event,
            "error": self.error,
            "final_output": self.final_output,
            "job_dir": os.fspath(self.job_dir),
        }


class CodexJobManager:
    def __init__(
        self,
        *,
        jobs_dir: Path,
        default_workspace: Path,
        docker_image: str,
        host_codex_home: Path,
        host_command: str = "codex",
        completion_callback: Optional[CodexCompletionCallback] = None,
        max_final_output_chars: int = 4000,
    ) -> None:
        self._jobs_dir = jobs_dir
        self._default_workspace = default_workspace
        self._docker_image = docker_image
        self._host_codex_home = host_codex_home
        self._host_command = host_command
        self._completion_callback = completion_callback
        self._max_final_output_chars = max_final_output_chars
        self._jobs: dict[str, CodexJob] = {}
        self._active_job_id: Optional[str] = None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [_start_codex_task_tool(), _get_codex_status_tool(), _cancel_codex_task_tool()]

    async def close(self) -> None:
        active_job = self.active_job()
        if active_job and active_job._process and active_job._process.returncode is None:
            active_job.status = "cancelling"
            active_job._process.terminate()
            try:
                await asyncio.wait_for(active_job._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                active_job._process.kill()

    def active_job(self) -> Optional[CodexJob]:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        if job is None or not job.is_active:
            return None
        return job

    async def execute_tool(self, name: str, arguments: dict[str, Any], *, origin_session_id: Optional[str]) -> dict[str, Any]:
        if name == "start_codex_task":
            return await self.start_task(arguments, origin_session_id=origin_session_id)
        if name == "get_codex_status":
            return self.get_status(str(arguments.get("job_id") or ""))
        if name == "cancel_codex_task":
            return await self.cancel_task(str(arguments.get("job_id") or ""))
        raise ValueError(f"Unsupported Codex tool: {name}")

    async def start_task(self, arguments: dict[str, Any], *, origin_session_id: Optional[str]) -> dict[str, Any]:
        active_job = self.active_job()
        if active_job is not None:
            return {
                "status": "busy",
                "active_job": active_job.as_tool_result(),
                "message": "Codex is already running one task. Ask for status or cancel it before starting another.",
            }

        task = str(arguments.get("task") or "").strip()
        if not task:
            return {"status": "error", "error": "A non-empty task is required."}

        execution_mode = str(arguments.get("execution_mode") or "docker").strip().lower()
        if execution_mode not in {"docker", "host"}:
            return {"status": "error", "error": "execution_mode must be docker or host."}
        if execution_mode == "host" and not bool(arguments.get("host_execution_confirmed")):
            return {
                "status": "needs_confirmation",
                "message": "Host execution needs explicit user confirmation. Ask whether to run Codex outside Docker, then retry with host_execution_confirmed=true.",
            }

        workspace = self._resolve_workspace(str(arguments.get("workspace") or ""))
        if not workspace.exists():
            return {"status": "error", "error": f"Workspace does not exist: {workspace}"}
        if not workspace.is_dir():
            return {"status": "error", "error": f"Workspace is not a directory: {workspace}"}

        job = self._create_job(task=task, workspace=workspace, execution_mode=execution_mode, origin_session_id=origin_session_id)
        self._jobs[job.id] = job
        self._active_job_id = job.id
        asyncio.create_task(self._run_job(job))
        _LOGGER.info("Started Codex job %s mode=%s workspace=%s", job.id, execution_mode, workspace)
        return {
            "status": "accepted",
            "job": job.as_tool_result(),
            "message": "Codex task accepted. The assistant will notify the requesting device when it finishes.",
        }

    def get_status(self, job_id: str = "") -> dict[str, Any]:
        job = self._resolve_job(job_id)
        if job is None:
            return {"status": "not_found", "message": "No Codex job was found."}
        return {"status": "ok", "job": job.as_tool_result()}

    async def cancel_task(self, job_id: str = "") -> dict[str, Any]:
        job = self._resolve_job(job_id)
        if job is None:
            return {"status": "not_found", "message": "No Codex job was found."}
        if not job.is_active or job._process is None:
            return {"status": "not_running", "job": job.as_tool_result()}
        job.status = "cancelling"
        job._process.terminate()
        try:
            await asyncio.wait_for(job._process.wait(), timeout=5)
        except asyncio.TimeoutError:
            job._process.kill()
        return {"status": "cancelling", "job": job.as_tool_result()}

    def _resolve_job(self, job_id: str) -> Optional[CodexJob]:
        if job_id:
            return self._jobs.get(job_id)
        if self._active_job_id and self._active_job_id in self._jobs:
            return self._jobs[self._active_job_id]
        if not self._jobs:
            return None
        return next(reversed(self._jobs.values()))

    def _resolve_workspace(self, workspace: str) -> Path:
        if not workspace:
            return self._default_workspace.expanduser().resolve()
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            path = self._default_workspace / path
        return path.resolve()

    def _create_job(self, *, task: str, workspace: Path, execution_mode: str, origin_session_id: Optional[str]) -> CodexJob:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = self._jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        return CodexJob(
            id=job_id,
            task=task,
            workspace=workspace,
            execution_mode=execution_mode,
            origin_session_id=origin_session_id,
            job_dir=job_dir,
            final_output_path=job_dir / "final.txt",
            events_path=job_dir / "events.jsonl",
            stderr_path=job_dir / "stderr.log",
        )

    async def _run_job(self, job: CodexJob) -> None:
        job.status = "running"
        job.started_at = time.time()
        metadata = {
            "id": job.id,
            "task": job.task,
            "workspace": os.fspath(job.workspace),
            "execution_mode": job.execution_mode,
            "origin_session_id": job.origin_session_id,
            "created_at": job.created_at,
        }
        (job.job_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        command = self._build_command(job)
        (job.job_dir / "command.txt").write_text(" ".join(shlex.quote(part) for part in command), encoding="utf-8")

        try:
            job._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert job._process.stdin is not None
            job._process.stdin.write(job.task.encode("utf-8"))
            job._process.stdin.write_eof()
            await asyncio.gather(self._capture_stdout(job), self._capture_stderr(job))
            job.return_code = await job._process.wait()
            if job.status == "cancelling":
                job.status = "cancelled"
            elif job.return_code == 0:
                job.status = "succeeded"
            else:
                job.status = "failed"
                job.error = f"Codex exited with code {job.return_code}"
            job.final_output = _read_limited(job.final_output_path, self._max_final_output_chars)
        except FileNotFoundError as err:
            job.status = "failed"
            job.error = f"Failed to start Codex command: {err}"
            _LOGGER.exception("Failed to start Codex job %s", job.id)
        except Exception as err:  # pylint: disable=broad-except
            job.status = "failed"
            job.error = str(err)
            _LOGGER.exception("Codex job %s crashed", job.id)
        finally:
            job.finished_at = time.time()
            job._process = None
            if self._active_job_id == job.id:
                self._active_job_id = None
            if not job.final_output:
                job.final_output = _read_limited(job.stderr_path, self._max_final_output_chars)
            if self._completion_callback is not None:
                try:
                    await self._completion_callback(job)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Codex completion callback failed for job %s", job.id)

    def _build_command(self, job: CodexJob) -> list[str]:
        if job.execution_mode == "host":
            return [
                self._host_command,
                "exec",
                "--json",
                "--output-last-message",
                os.fspath(job.final_output_path),
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                "--skip-git-repo-check",
                "-C",
                os.fspath(job.workspace),
                "-",
            ]

        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/codex-home",
        ]
        for env_name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT"):
            if os.getenv(env_name):
                command.extend(["-e", env_name])
        command.extend(
            [
                "-v",
                f"{job.workspace}:/workspace",
                "-v",
                f"{job.job_dir}:/job",
                "-v",
                f"{self._host_codex_home.expanduser()}:/codex-home/.codex",
                "-w",
                "/workspace",
                self._docker_image,
                "codex",
                "exec",
                "--json",
                "--output-last-message",
                "/job/final.txt",
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                "--skip-git-repo-check",
                "-C",
                "/workspace",
                "-",
            ]
        )
        return command

    async def _capture_stdout(self, job: CodexJob) -> None:
        assert job._process is not None
        assert job._process.stdout is not None
        with job.events_path.open("ab") as events_file:
            while True:
                line = await job._process.stdout.readline()
                if not line:
                    return
                events_file.write(line)
                events_file.flush()
                summary = summarize_codex_event(line.decode("utf-8", errors="replace"))
                if summary:
                    job.last_event = summary

    async def _capture_stderr(self, job: CodexJob) -> None:
        assert job._process is not None
        assert job._process.stderr is not None
        with job.stderr_path.open("ab") as stderr_file:
            while True:
                line = await job._process.stderr.readline()
                if not line:
                    return
                stderr_file.write(line)
                stderr_file.flush()
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    job.last_event = text[:500]


class CodexAgentTool:
    def __init__(self, manager: CodexJobManager, origin_session_id: Optional[str]) -> None:
        self._manager = manager
        self._origin_session_id = origin_session_id

    def tool_definitions(self) -> list[dict[str, Any]]:
        return self._manager.tool_definitions()

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._manager.execute_tool(name, arguments, origin_session_id=self._origin_session_id)

    async def close(self) -> None:
        return None


def summarize_codex_event(raw_line: str) -> str:
    stripped = raw_line.strip()
    if not stripped:
        return ""
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped[:500]

    if not isinstance(event, dict):
        return stripped[:500]

    event_type = str(event.get("type") or event.get("event") or "").strip()
    for key in ("message", "msg", "text", "summary", "status"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return _compact(f"{event_type}: {value}" if event_type else value)
    if "payload" in event:
        return _compact(f"{event_type}: {event['payload']}" if event_type else str(event["payload"]))
    return _compact(str(event))


def _start_codex_task_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "start_codex_task",
        "description": (
            "Dispatch an asynchronous task to a Codex coding agent. Use when the user asks Codex or an agent to do software work. "
            "Default to Docker execution. If the task needs host access outside Docker, ask the user for explicit confirmation first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The complete task Codex should perform, including repo/path, expected changes, and verification if known.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Optional workspace directory. Ask a follow-up if the target project is ambiguous.",
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["docker", "host"],
                    "default": "docker",
                    "description": "Run in Docker unless the user explicitly confirms host execution.",
                },
                "host_execution_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": "True only after the user clearly agreed to running Codex outside Docker for this task.",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    }


def _get_codex_status_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "get_codex_status",
        "description": "Check whether the current or requested Codex task is still running and summarize its latest activity.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Optional Codex job id. Omit to inspect the active or most recent job."},
            },
            "additionalProperties": False,
        },
    }


def _cancel_codex_task_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "cancel_codex_task",
        "description": "Cancel the active or requested Codex task.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Optional Codex job id. Omit to cancel the active job."},
            },
            "additionalProperties": False,
        },
    }


def _read_limited(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 40].rstrip() + "\n[output truncated]"


def _compact(value: str) -> str:
    return " ".join(value.split())[:500]


def _elapsed_seconds(job: CodexJob) -> float:
    start = job.started_at or job.created_at
    end = job.finished_at or time.time()
    return max(0.0, end - start)
