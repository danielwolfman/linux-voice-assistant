import time
from datetime import datetime, timezone

from linux_voice_assistant.tools.running_coach import (
    ACTIVE_ENERGY_ENTITY,
    HEART_RATE_AVG_ENTITY,
    HEART_RATE_MAX_ENTITY,
    RUN_DISTANCE_ENTITY,
    STEP_COUNT_ENTITY,
    WALKING_SPEED_ENTITY,
    build_tally_running_coach_task,
    parse_history_response,
    summarize_running_history,
)


def test_running_history_requires_recent_heart_rate_stats():
    now = time.time()
    walk_ts = now - 3600
    run_ts = now - 1200
    raw = [
        [
            _state(RUN_DISTANCE_ENTITY, walk_ts, "1.81"),
            _state(RUN_DISTANCE_ENTITY, run_ts, "3.2"),
        ],
        [
            _state(HEART_RATE_AVG_ENTITY, run_ts + 10, "142"),
        ],
        [
            _state(HEART_RATE_MAX_ENTITY, run_ts + 20, "171"),
        ],
        [
            _state(WALKING_SPEED_ENTITY, run_ts, "8.0"),
        ],
        [
            _state(ACTIVE_ENERGY_ENTITY, run_ts, "640"),
        ],
        [
            _state(STEP_COUNT_ENTITY, run_ts, "4300"),
        ],
    ]

    samples = parse_history_response(raw)
    summary = summarize_running_history(samples, now_ts=now)

    assert summary["latest_run"]["distance_km"] == 3.2
    assert summary["today"]["run_count"] == 1
    assert summary["today"]["total_km"] == 3.2
    assert summary["last_7_days"]["total_km"] == 3.2


def test_running_coach_task_instructs_codex_to_write_hebrew_message_only():
    task = build_tally_running_coach_task(
        {"distance_km": 3.2},
        {"today": {"total_km": 3.2}, "latest_run": {"distance_km": 3.2}},
    )

    assert "Write only the final message text in Hebrew" in task
    assert "טלי" in task
    assert '"distance_km": 3.2' in task


def _state(entity_id, ts, state):
    return {
        "entity_id": entity_id,
        "state": state,
        "last_updated": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
    }
