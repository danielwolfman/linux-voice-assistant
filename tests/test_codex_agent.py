import asyncio

from linux_voice_assistant.tools.codex_agent import CodexJobManager, summarize_codex_event


def test_summarize_codex_event_handles_json_message():
    summary = summarize_codex_event('{"type":"agent_message","message":"working on tests"}\n')

    assert summary == "agent_message: working on tests"


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


def test_codex_manager_uses_absolute_job_dir(tmp_path):
    manager = CodexJobManager(
        jobs_dir=tmp_path / "relative" / ".." / "jobs",
        default_workspace=tmp_path,
        docker_image="lva-codex-agent:latest",
        host_codex_home=tmp_path / ".codex",
    )

    job = manager._create_job(task="inspect", workspace=tmp_path, execution_mode="docker", origin_session_id="session-1")
    command = manager._build_command(job)

    assert job.job_dir.is_absolute()
    assert f"{job.job_dir}:/job" in command
    assert "OPENAI_API_KEY" not in command
    assert command[command.index("codex") + 1 : command.index("codex") + 4] == ["--ask-for-approval", "never", "exec"]
