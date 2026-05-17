"""Asynchronous Codex CLI job runner exposed as Realtime tools."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

CodexCompletionCallback = Callable[["CodexJob"], Awaitable[None]]


@dataclass
class CodexJob:
    id: str
    task: str
    workspace: Path
    execution_mode: str
    origin_session_id: Optional[str]
    origin_language: str = ""
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
    app_server_thread_id: str = ""
    app_server_turn_id: str = ""
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False, compare=False)
    _ws: Any = field(default=None, repr=False, compare=False)

    @property
    def is_active(self) -> bool:
        return self.status in {"queued", "running", "cancelling"}

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "origin_language": self.origin_language,
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
        host_gh_config_dir: Path | None = None,
        host_command: str = "codex",
        dispatch_mode: str = "exec",
        app_server_command: str = "codex",
        app_server_url: str = "",
        app_server_client_name: str = "codex_chatgpt_android_remote",
        app_server_client_version: str = "dev",
        app_server_thread_source: str = "",
        app_server_service_name: str = "",
        completion_callback: Optional[CodexCompletionCallback] = None,
        max_final_output_chars: int = 4000,
    ) -> None:
        self._jobs_dir = jobs_dir
        self._default_workspace = default_workspace
        self._docker_image = docker_image
        self._host_codex_home = host_codex_home
        self._host_gh_config_dir = host_gh_config_dir
        self._host_command = host_command
        self._dispatch_mode = dispatch_mode.strip().lower()
        self._app_server_command = app_server_command
        self._app_server_url = app_server_url.strip()
        self._app_server_client_name = app_server_client_name.strip() or "codex_chatgpt_android_remote"
        self._app_server_client_version = app_server_client_version.strip() or "dev"
        self._app_server_thread_source = app_server_thread_source.strip()
        self._app_server_service_name = app_server_service_name.strip()
        self._completion_callback = completion_callback
        self._max_final_output_chars = max_final_output_chars
        self._jobs: dict[str, CodexJob] = {}
        self._active_job_id: Optional[str] = None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [_start_codex_task_tool(), _get_codex_status_tool(), _cancel_codex_task_tool()]

    async def close(self) -> None:
        for active_job in self._active_jobs():
            if active_job._ws is not None and not active_job._ws.closed:
                active_job.status = "cancelling"
                await active_job._ws.close()
            if active_job._process and active_job._process.returncode is None:
                active_job.status = "cancelling"
                active_job._process.terminate()
                try:
                    await asyncio.wait_for(active_job._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    active_job._process.kill()

    def active_job(self) -> Optional[CodexJob]:
        if self._active_job_id is None:
            return self._latest_active_job()
        job = self._jobs.get(self._active_job_id)
        if job is None or not job.is_active:
            return self._latest_active_job()
        return job

    def _active_jobs(self) -> list[CodexJob]:
        return [job for job in self._jobs.values() if job.is_active]

    def _latest_active_job(self) -> Optional[CodexJob]:
        active_jobs = self._active_jobs()
        if not active_jobs:
            return None
        return active_jobs[-1]

    def _refresh_active_job_id(self) -> None:
        latest_active = self._latest_active_job()
        self._active_job_id = latest_active.id if latest_active is not None else None

    async def execute_tool(self, name: str, arguments: dict[str, Any], *, origin_session_id: Optional[str]) -> dict[str, Any]:
        if name == "start_codex_task":
            return await self.start_task(arguments, origin_session_id=origin_session_id)
        if name == "get_codex_status":
            return self.get_status(str(arguments.get("job_id") or ""))
        if name == "cancel_codex_task":
            return await self.cancel_task(str(arguments.get("job_id") or ""))
        raise ValueError(f"Unsupported Codex tool: {name}")

    async def start_task(self, arguments: dict[str, Any], *, origin_session_id: Optional[str], allow_parallel: bool = False) -> dict[str, Any]:
        active_job = self.active_job()
        if active_job is not None and not allow_parallel:
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
        resolved_execution_mode = self._resolve_execution_mode(execution_mode)

        workspace = self._resolve_workspace(str(arguments.get("workspace") or ""))
        if not workspace.exists():
            return {"status": "error", "error": f"Workspace does not exist: {workspace}"}
        if not workspace.is_dir():
            return {"status": "error", "error": f"Workspace is not a directory: {workspace}"}

        origin_language = _normalize_language(str(arguments.get("origin_language") or "")) or _detect_language(task)
        job = self._create_job(
            task=task,
            workspace=workspace,
            execution_mode=resolved_execution_mode,
            origin_session_id=origin_session_id,
            origin_language=origin_language,
        )
        self._jobs[job.id] = job
        self._active_job_id = job.id
        asyncio.create_task(self._run_job(job))
        _LOGGER.info("Started Codex job %s mode=%s requested_mode=%s workspace=%s", job.id, resolved_execution_mode, execution_mode, workspace)
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
        if job.execution_mode == "app_server":
            await self._interrupt_app_server_job(job)
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

    def _resolve_execution_mode(self, requested_mode: str) -> str:
        if self._dispatch_mode == "app_server":
            return "app_server"
        return requested_mode

    def _create_job(
        self,
        *,
        task: str,
        workspace: Path,
        execution_mode: str,
        origin_session_id: Optional[str],
        origin_language: str,
    ) -> CodexJob:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = (self._jobs_dir / job_id).expanduser().resolve()
        job_dir.mkdir(parents=True, exist_ok=False)
        return CodexJob(
            id=job_id,
            task=task,
            workspace=workspace,
            execution_mode=execution_mode,
            origin_session_id=origin_session_id,
            origin_language=origin_language,
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
            "origin_language": job.origin_language,
            "created_at": job.created_at,
        }
        (job.job_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        if job.execution_mode == "app_server":
            await self._run_app_server_job(job)
            return

        command = self._build_command(job)
        (job.job_dir / "command.txt").write_text(" ".join(shlex.quote(part) for part in command), encoding="utf-8")

        try:
            if job.execution_mode == "docker":
                self._check_docker_preflight(job)
            process_env = _codex_process_env()
            job._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=process_env,
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
                self._refresh_active_job_id()
            if not job.final_output:
                job.final_output = _read_limited(job.stderr_path, self._max_final_output_chars)
            if self._completion_callback is not None:
                try:
                    await self._completion_callback(job)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Codex completion callback failed for job %s", job.id)

    async def _run_app_server_job(self, job: CodexJob) -> None:
        command = [self._app_server_command, "app-server", "--listen", "stdio://"]
        if self._app_server_url:
            command = ["app-server-websocket", self._app_server_url]
        (job.job_dir / "command.txt").write_text(" ".join(shlex.quote(part) for part in command), encoding="utf-8")
        request_id = 0
        final_output = ""
        session: aiohttp.ClientSession | None = None
        stderr_task: asyncio.Task[None] | None = None

        try:
            if self._app_server_url:
                session = aiohttp.ClientSession()
                job._ws = await session.ws_connect(self._app_server_url)

                async def send_transport_line(payload: str) -> None:
                    await job._ws.send_str(payload)

                async def read_transport_line() -> bytes:
                    message = await job._ws.receive()
                    if message.type == aiohttp.WSMsgType.TEXT:
                        return (message.data + "\n").encode("utf-8")
                    if message.type == aiohttp.WSMsgType.ERROR:
                        raise RuntimeError(str(job._ws.exception()))
                    return b""

            else:
                process_env = _codex_process_env()
                job._process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=process_env,
                )
                assert job._process.stdin is not None
                assert job._process.stdout is not None
                stderr_task = asyncio.create_task(self._capture_stderr(job))

                async def send_transport_line(payload: str) -> None:
                    assert job._process is not None
                    assert job._process.stdin is not None
                    job._process.stdin.write(payload.encode("utf-8"))
                    await job._process.stdin.drain()

                async def read_transport_line() -> bytes:
                    assert job._process is not None
                    assert job._process.stdout is not None
                    return await job._process.stdout.readline()

            async def send_request(method: str, params: dict[str, Any]) -> int:
                nonlocal request_id
                request_id += 1
                payload = {"id": request_id, "method": method, "params": params}
                await send_transport_line(json.dumps(payload) + "\n")
                return request_id

            initialize_id = await send_request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self._app_server_client_name,
                        "version": self._app_server_client_version,
                    },
                    "capabilities": {},
                },
            )
            thread_start_params: dict[str, Any] = {
                "cwd": os.fspath(job.workspace),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
            }
            if self._app_server_thread_source:
                thread_start_params["threadSource"] = self._app_server_thread_source
            if self._app_server_service_name:
                thread_start_params["serviceName"] = self._app_server_service_name
            thread_start_id = await send_request(
                "thread/start",
                thread_start_params,
            )
            turn_start_id = 0

            with job.events_path.open("ab") as events_file:
                while True:
                    line = await read_transport_line()
                    if not line:
                        if job.status == "cancelling":
                            job.status = "cancelled"
                            job.return_code = -15
                        else:
                            job.status = "failed"
                            if job._process is not None:
                                job.return_code = await job._process.wait()
                            else:
                                job.return_code = 1
                            job.error = f"Codex app-server exited before the turn completed with code {job.return_code}"
                        break

                    events_file.write(line)
                    events_file.flush()
                    text = line.decode("utf-8", errors="replace")
                    summary = summarize_app_server_event(text)
                    if summary:
                        job.last_event = summary

                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue

                    error = _json_rpc_error_message(event)
                    if error:
                        job.status = "failed"
                        job.return_code = 1
                        job.error = error
                        break

                    if event.get("id") == initialize_id:
                        continue

                    if event.get("id") == thread_start_id:
                        thread_id = _extract_app_server_thread_id(event.get("result"))
                        if not thread_id:
                            job.status = "failed"
                            job.return_code = 1
                            job.error = "Codex app-server did not return a thread id."
                            break
                        job.app_server_thread_id = thread_id
                        job.last_event = f"Codex app-server thread started: {thread_id}"
                        turn_start_id = await send_request(
                            "turn/start",
                            {
                                "threadId": thread_id,
                                "cwd": os.fspath(job.workspace),
                                "approvalPolicy": "never",
                                "sandboxPolicy": {
                                    "type": "workspaceWrite",
                                    "writableRoots": [os.fspath(job.workspace)],
                                    "networkAccess": True,
                                    "excludeTmpdirEnvVar": False,
                                    "excludeSlashTmp": False,
                                },
                                "input": [{"type": "text", "text": job.task, "text_elements": []}],
                            },
                        )
                        continue

                    if event.get("id") == turn_start_id:
                        turn_id = _extract_app_server_turn_id(event.get("result"))
                        if turn_id:
                            job.app_server_turn_id = turn_id
                            job.last_event = f"Codex app-server turn started: {turn_id}"
                        continue

                    method = str(event.get("method") or "")
                    params = event.get("params") if isinstance(event.get("params"), dict) else {}
                    if method == "turn/started":
                        turn_id = _extract_app_server_turn_id(params)
                        if turn_id:
                            job.app_server_turn_id = turn_id
                    elif method == "item/agentMessage/delta":
                        delta = str(params.get("delta") or "")
                        if delta:
                            final_output += delta
                            job.final_output = _limit_text(final_output, self._max_final_output_chars)
                    elif method == "item/completed":
                        item = params.get("item")
                        if isinstance(item, dict) and item.get("type") == "agentMessage":
                            item_text = str(item.get("text") or "")
                            if item_text:
                                final_output = item_text
                                job.final_output = _limit_text(final_output, self._max_final_output_chars)
                    elif method == "turn/completed":
                        turn = params.get("turn")
                        turn_status = str(turn.get("status") if isinstance(turn, dict) else "")
                        if turn_status == "completed":
                            job.status = "succeeded"
                            job.return_code = 0
                        elif job.status == "cancelling":
                            job.status = "cancelled"
                            job.return_code = -15
                        else:
                            job.status = "failed"
                            job.return_code = 1
                            if isinstance(turn, dict) and turn.get("error"):
                                job.error = str(turn.get("error"))
                            else:
                                job.error = f"Codex app-server turn ended with status: {turn_status or 'unknown'}"
                        break

            if job.final_output:
                job.final_output_path.write_text(job.final_output, encoding="utf-8")
            if job._ws is not None and not job._ws.closed:
                await job._ws.close()
            if job._process is not None and job._process.returncode is None:
                job._process.terminate()
                try:
                    await asyncio.wait_for(job._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    job._process.kill()
                    await job._process.wait()
            if stderr_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task
            if session is not None:
                await session.close()
        except FileNotFoundError as err:
            job.status = "failed"
            job.error = f"Failed to start Codex app-server command: {err}"
            _LOGGER.exception("Failed to start Codex app-server job %s", job.id)
        except Exception as err:  # pylint: disable=broad-except
            job.status = "failed"
            job.error = str(err)
            _LOGGER.exception("Codex app-server job %s crashed", job.id)
        finally:
            job.finished_at = time.time()
            job._process = None
            job._ws = None
            if session is not None and not session.closed:
                await session.close()
            if self._active_job_id == job.id:
                self._refresh_active_job_id()
            if not job.final_output:
                job.final_output = _read_limited(job.stderr_path, self._max_final_output_chars)
            if self._completion_callback is not None:
                try:
                    await self._completion_callback(job)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Codex completion callback failed for job %s", job.id)

    async def _interrupt_app_server_job(self, job: CodexJob) -> None:
        if not job.app_server_thread_id:
            return
        payload: dict[str, Any] = {"threadId": job.app_server_thread_id}
        if job.app_server_turn_id:
            payload["turnId"] = job.app_server_turn_id
        if job._ws is not None and not job._ws.closed:
            await job._ws.send_str(json.dumps({"id": int(time.time() * 1000), "method": "turn/interrupt", "params": payload}) + "\n")
            return
        if job._process is None or job._process.stdin is None:
            return
        try:
            job._process.stdin.write(
                (json.dumps({"id": int(time.time() * 1000), "method": "turn/interrupt", "params": payload}) + "\n").encode("utf-8")
            )
            await job._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _build_command(self, job: CodexJob) -> list[str]:
        if job.execution_mode == "host":
            return [
                self._host_command,
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--output-last-message",
                os.fspath(job.final_output_path),
                "--sandbox",
                "workspace-write",
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
        for group_id in _supplementary_group_ids():
            command.extend(["--group-add", str(group_id)])
        command.extend(
            [
                "-v",
                f"{job.workspace}:/workspace",
                "-v",
                f"{job.job_dir}:/job",
                "-v",
                f"{self._host_codex_home.expanduser()}:/codex-home/.codex",
            ]
        )
        gh_config_dir = self._resolved_gh_config_dir()
        if gh_config_dir is not None:
            command.extend(
                [
                    "-v",
                    f"{gh_config_dir}:/codex-home/.config/gh",
                    "-e",
                    "GH_CONFIG_DIR=/codex-home/.config/gh",
                ]
            )
        command.extend(
            [
                "-w",
                "/workspace",
                self._docker_image,
                "codex",
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--output-last-message",
                "/job/final.txt",
                "--sandbox",
                "danger-full-access",
                "--skip-git-repo-check",
                "-C",
                "/workspace",
                "-",
            ]
        )
        return command

    def _resolved_gh_config_dir(self) -> Path | None:
        if self._host_gh_config_dir is None:
            return None
        path = self._host_gh_config_dir.expanduser()
        return path if path.exists() else None

    def _check_docker_preflight(self, job: CodexJob) -> None:
        problems = []
        if shutil.which("docker") is None:
            problems.append("docker executable was not found in PATH")
        else:
            try:
                docker_check = subprocess.run(
                    ["docker", "version", "--format", "{{.Server.Version}}"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if docker_check.returncode != 0:
                    docker_error = docker_check.stderr.strip() or docker_check.stdout.strip()
                    problems.append(f"Docker daemon is not reachable: {docker_error}")
            except (OSError, subprocess.SubprocessError) as err:
                problems.append(f"Docker daemon check failed: {err}")

        for label, path, needs_write in (
            ("workspace", job.workspace, True),
            ("job directory", job.job_dir, True),
            ("Codex home", self._host_codex_home.expanduser(), True),
        ):
            resolved = path.expanduser()
            if not resolved.exists():
                problems.append(f"{label} does not exist: {resolved}")
                continue
            access = os.R_OK | os.X_OK | (os.W_OK if needs_write else 0)
            if not os.access(resolved, access):
                try:
                    stat_result = resolved.stat()
                    detail = f"owner={stat_result.st_uid}:{stat_result.st_gid} mode={oct(stat_result.st_mode & 0o777)}"
                except OSError as err:
                    detail = f"stat failed: {err}"
                problems.append(
                    f"{label} is not accessible by uid {os.getuid()} gid {os.getgid()}: {resolved} {detail}"
                )

        gh_config_dir = self._resolved_gh_config_dir()
        if gh_config_dir is None:
            configured_gh_config_dir = self._host_gh_config_dir.expanduser() if self._host_gh_config_dir is not None else None
            if configured_gh_config_dir is not None:
                _LOGGER.info("GitHub CLI config directory is not mounted for Codex Docker jobs because it does not exist: %s", configured_gh_config_dir)
        elif not os.access(gh_config_dir, os.R_OK | os.X_OK):
            try:
                stat_result = gh_config_dir.stat()
                detail = f"owner={stat_result.st_uid}:{stat_result.st_gid} mode={oct(stat_result.st_mode & 0o777)}"
            except OSError as err:
                detail = f"stat failed: {err}"
            problems.append(
                f"GitHub CLI config directory is not readable by uid {os.getuid()} gid {os.getgid()}: {gh_config_dir} {detail}"
            )

        if problems:
            detail = "\n".join(f"- {problem}" for problem in problems)
            message = (
                "Docker Codex preflight failed:\n"
                f"{detail}\n"
                f"- effective uid/gid: {os.getuid()}:{os.getgid()}\n"
                f"- supplementary groups: {_format_group_ids()}\n"
            )
            job.stderr_path.write_text(message, encoding="utf-8")
            raise RuntimeError(message)

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
    def __init__(
        self,
        manager: CodexJobManager,
        origin_session_id: Optional[str],
        origin_language_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self._manager = manager
        self._origin_session_id = origin_session_id
        self._origin_language_provider = origin_language_provider

    def tool_definitions(self) -> list[dict[str, Any]]:
        return self._manager.tool_definitions()

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "start_codex_task" and not arguments.get("origin_language") and self._origin_language_provider is not None:
            origin_language = self._origin_language_provider()
            if origin_language:
                arguments = dict(arguments)
                arguments["origin_language"] = origin_language
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


def summarize_app_server_event(raw_line: str) -> str:
    stripped = raw_line.strip()
    if not stripped:
        return ""
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped[:500]
    if not isinstance(event, dict):
        return stripped[:500]
    error = _json_rpc_error_message(event)
    if error:
        return error
    method = str(event.get("method") or "").strip()
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    if method == "item/agentMessage/delta":
        return "Codex is writing the final answer"
    if method == "item/started":
        item = params.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage":
                return "Codex is drafting a response"
            if item_type == "commandExecution":
                command = item.get("command")
                if isinstance(command, str) and command.strip():
                    return _compact(f"Codex is running: {command}")
    if method == "turn/started":
        return "Codex turn started"
    if method == "turn/completed":
        turn = params.get("turn")
        status = turn.get("status") if isinstance(turn, dict) else ""
        return _compact(f"Codex turn completed: {status or 'unknown'}")
    if method == "remoteControl/status/changed":
        status = params.get("status")
        return _compact(f"Codex cloud sync: {status}")
    if method == "mcpServer/startupStatus/updated":
        return _compact(f"MCP {params.get('name')}: {params.get('status')}")
    if method == "warning" and isinstance(params.get("message"), str):
        return _compact(str(params["message"]))
    if method:
        return _compact(method)
    return _compact(str(event))


def _json_rpc_error_message(event: dict[str, Any]) -> str:
    error = event.get("error")
    if error is None:
        return ""
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or error)
    else:
        message = str(error)
    return _compact(f"Codex app-server error: {message}")


def _extract_app_server_thread_id(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    thread_id = result.get("threadId") or result.get("id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    thread = result.get("thread")
    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
        return str(thread["id"])
    return ""


def _extract_app_server_turn_id(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    turn_id = result.get("turnId") or result.get("id")
    if isinstance(turn_id, str) and turn_id:
        return turn_id
    turn = result.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str):
        return str(turn["id"])
    return ""


def _start_codex_task_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "start_codex_task",
        "description": (
            "Dispatch an asynchronous task to a Codex coding agent. Use when the user asks Codex or an agent to do software work. "
            "Use the configured default Codex execution backend. If the user does not name a repo or workspace, omit workspace so the configured default workspace is used. "
            "Do not ask which repo unless the user explicitly refers to another repo ambiguously. "
            "If the task needs host access outside the configured default sandbox/container, ask the user for explicit confirmation first."
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
                    "description": "Optional workspace directory. Omit to use the configured default workspace; do not ask the user for a repo only because this is missing.",
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["docker", "host"],
                    "default": "docker",
                    "description": "Request the default contained/sandboxed mode unless the user explicitly confirms host execution.",
                },
                "host_execution_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": "True only after the user clearly agreed to running Codex outside Docker for this task.",
                },
                "origin_language": {
                    "type": "string",
                    "enum": ["he", "en"],
                    "description": "Language of the user's spoken request. Use he for Hebrew and en for English.",
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
    return _limit_text(text, limit)


def _limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40].rstrip() + "\n[output truncated]"


def _compact(value: str) -> str:
    return " ".join(value.split())[:500]


def _elapsed_seconds(job: CodexJob) -> float:
    start = job.started_at or job.created_at
    end = job.finished_at or time.time()
    return max(0.0, end - start)


def _normalize_language(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in {"he", "heb", "hebrew", "iw"}:
        return "he"
    if normalized in {"en", "eng", "english"}:
        return "en"
    return ""


def _detect_language(text: str) -> str:
    return "he" if any("\u0590" <= char <= "\u05ff" for char in text) else "en"


def _supplementary_group_ids() -> list[int]:
    return sorted(set(os.getgroups()))


def _format_group_ids() -> str:
    return ", ".join(str(group_id) for group_id in _supplementary_group_ids()) or "none"


def _codex_process_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT"):
        env.pop(key, None)
    return env
