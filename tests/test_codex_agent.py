import asyncio

from linux_voice_assistant.tools import codex_agent
from linux_voice_assistant.tools.codex_agent import CodexAgentTool, CodexJobManager, summarize_app_server_event, summarize_codex_event


def test_summarize_codex_event_handles_json_message():
    summary = summarize_codex_event('{"type":"agent_message","message":"working on tests"}\n')

    assert summary == "agent_message: working on tests"


def test_summarize_app_server_event_handles_final_answer_delta():
    summary = summarize_app_server_event('{"method":"item/agentMessage/delta","params":{"delta":"OK"}}\n')

    assert summary == "Codex is writing the final answer"


def test_codex_manager_rejects_host_without_confirmation(tmp_path):
    async def run():
        manager = CodexJobManager(
            jobs_dir=tmp_path / "jobs",
            default_workspace=tmp_path,
            docker_image="lva-codex-agent:latest",
            host_codex_home=tmp_path / ".codex",
        )

        result = await manager.start_task(
            {"task": "inspect the repo", "execution_mode": "host"},
            origin_session_id="session-1",
        )

        assert result["status"] == "needs_confirmation"

    asyncio.run(run())


def test_codex_manager_accepts_docker_job_and_reports_busy(tmp_path):
    async def run():
        manager = CodexJobManager(
            jobs_dir=tmp_path / "jobs",
            default_workspace=tmp_path,
            docker_image="lva-codex-agent:latest",
            host_codex_home=tmp_path / ".codex",
        )
        started = []

        async def fake_run(job):
            started.append(job)

        manager._run_job = fake_run

        first = await manager.start_task({"task": "inspect the repo"}, origin_session_id="session-1")
        second = await manager.start_task({"task": "inspect again"}, origin_session_id="session-1")
        await asyncio.sleep(0)

        assert first["status"] == "accepted"
        assert second["status"] == "busy"
        assert started[0].origin_session_id == "session-1"

    asyncio.run(run())


def test_codex_manager_maps_default_jobs_to_app_server_when_configured(tmp_path):
    async def run():
        manager = CodexJobManager(
            jobs_dir=tmp_path / "jobs",
            default_workspace=tmp_path,
            docker_image="lva-codex-agent:latest",
            host_codex_home=tmp_path / ".codex",
            dispatch_mode="app_server",
            app_server_command="/home/daniel/.local/bin/codex",
        )
        started = []

        async def fake_run(job):
            started.append(job)

        manager._run_job = fake_run

        result = await manager.start_task({"task": "inspect the repo"}, origin_session_id="session-1")
        await asyncio.sleep(0)

        assert result["status"] == "accepted"
        assert result["job"]["execution_mode"] == "app_server"
        assert started[0].execution_mode == "app_server"

    asyncio.run(run())


def test_codex_agent_tool_adds_origin_language_from_current_transcript(tmp_path):
    async def run():
        manager = CodexJobManager(
            jobs_dir=tmp_path / "jobs",
            default_workspace=tmp_path,
            docker_image="lva-codex-agent:latest",
            host_codex_home=tmp_path / ".codex",
        )
        started = []

        async def fake_run(job):
            started.append(job)

        manager._run_job = fake_run
        tool = CodexAgentTool(manager, "session-1", lambda: "he")

        result = await tool.execute_tool("start_codex_task", {"task": "fix the tests"})
        await asyncio.sleep(0)

        assert result["status"] == "accepted"
        assert started[0].origin_language == "he"

    asyncio.run(run())


def test_codex_manager_infers_hebrew_language_from_task_when_not_provided(tmp_path):
    async def run():
        manager = CodexJobManager(
            jobs_dir=tmp_path / "jobs",
            default_workspace=tmp_path,
            docker_image="lva-codex-agent:latest",
            host_codex_home=tmp_path / ".codex",
        )
        started = []

        async def fake_run(job):
            started.append(job)

        manager._run_job = fake_run

        result = await manager.start_task({"task": "תתקן את הטסטים"}, origin_session_id="session-1")
        await asyncio.sleep(0)

        assert result["status"] == "accepted"
        assert started[0].origin_language == "he"

    asyncio.run(run())


def test_codex_manager_uses_absolute_job_dir_and_docker_container_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_agent.os, "getgroups", lambda: [115, 1000, 115])
    gh_config = tmp_path / ".config" / "gh"
    gh_config.mkdir(parents=True)
    manager = CodexJobManager(
        jobs_dir=tmp_path / "relative" / ".." / "jobs",
        default_workspace=tmp_path,
        docker_image="lva-codex-agent:latest",
        host_codex_home=tmp_path / ".codex",
        host_gh_config_dir=gh_config,
    )

    job = manager._create_job(
        task="inspect",
        workspace=tmp_path,
        execution_mode="docker",
        origin_session_id="session-1",
        origin_language="en",
    )
    command = manager._build_command(job)

    assert job.job_dir.is_absolute()
    assert f"{job.job_dir}:/job" in command
    assert "OPENAI_API_KEY" not in command
    assert "--group-add" in command
    assert command[command.index("--group-add") + 1] == "115"
    assert f"{gh_config}:/codex-home/.config/gh" in command
    assert "GH_CONFIG_DIR=/codex-home/.config/gh" in command
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert command[command.index("codex") + 1 : command.index("codex") + 4] == [
        "--ask-for-approval",
        "never",
        "exec",
    ]
