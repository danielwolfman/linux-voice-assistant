"""OpenAI usage polling for the OpenAI Real Time Assistant integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

PRICING = {
    "gpt-realtime-1.5": {
        "audio_input": 32.00,
        "text_input": 4.00,
        "text_cached_input": 0.40,
        "audio_output": 64.00,
        "text_output": 16.00,
    },
    "gpt-realtime-mini": {
        "audio_input": 10.00,
        "text_input": 0.60,
        "text_cached_input": 0.06,
        "audio_output": 20.00,
        "text_output": 2.40,
    },
}


async def fetch_usage_summaries(admin_api_key: str) -> dict[str, dict[str, float | int]]:
    now = datetime.now(tz=UTC)
    start_24h = int((now - timedelta(hours=24)).timestamp())
    params = {
        "start_time": str(start_24h),
        "bucket_width": "1h",
        "group_by": ["model"],
        "limit": "24",
    }
    headers = {"Authorization": f"Bearer {admin_api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get("https://api.openai.com/v1/organization/usage/completions", params=params) as response:
            response.raise_for_status()
            payload = await response.json()

    buckets = payload.get("data", [])
    last_24h = _aggregate_usage_buckets(buckets, hours=24)
    last_1h = _aggregate_usage_buckets(buckets, hours=1)
    return {"usage_last_hour": last_1h, "usage_last_24_hours": last_24h}


def _aggregate_usage_buckets(buckets: list[dict[str, Any]], hours: int) -> dict[str, float | int]:
    relevant = buckets[-hours:]
    summary = {
        "count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }
    for bucket in relevant:
        for result in bucket.get("results", []):
            model = str(result.get("model") or "")
            if not _is_realtime_model(model):
                continue
            usage = {
                "input_tokens": int(result.get("input_tokens", 0)),
                "output_tokens": int(result.get("output_tokens", 0)),
                "total_tokens": int(result.get("input_tokens", 0)) + int(result.get("output_tokens", 0)),
                "cached_input_tokens": int(result.get("input_cached_tokens", 0)),
                "input_audio_tokens": int(result.get("input_audio_tokens", 0)),
                "output_audio_tokens": int(result.get("output_audio_tokens", 0)),
            }
            usage["input_text_tokens"] = max(0, usage["input_tokens"] - usage["input_audio_tokens"])
            usage["output_text_tokens"] = max(0, usage["output_tokens"] - usage["output_audio_tokens"])
            summary["count"] += int(result.get("num_model_requests", 0))
            summary["input_tokens"] += usage["input_tokens"]
            summary["output_tokens"] += usage["output_tokens"]
            summary["total_tokens"] += usage["total_tokens"]
            summary["cost_usd"] += estimate_cost(model, usage)
    return summary


def estimate_cost(model: str, usage: dict[str, int]) -> float:
    pricing_key = resolve_pricing_model(model)
    pricing = PRICING.get(pricing_key)
    if pricing is None:
        return 0.0
    cached_input_tokens = min(usage.get("cached_input_tokens", 0), usage.get("input_text_tokens", 0))
    uncached_input_text_tokens = max(0, usage.get("input_text_tokens", 0) - cached_input_tokens)
    return (
        (usage.get("input_audio_tokens", 0) / 1_000_000) * pricing["audio_input"]
        + (cached_input_tokens / 1_000_000) * pricing["text_cached_input"]
        + (uncached_input_text_tokens / 1_000_000) * pricing["text_input"]
        + (usage.get("output_audio_tokens", 0) / 1_000_000) * pricing["audio_output"]
        + (usage.get("output_text_tokens", 0) / 1_000_000) * pricing["text_output"]
    )


def resolve_pricing_model(model: str) -> str:
    if model.startswith("gpt-realtime-mini"):
        return "gpt-realtime-mini"
    if model.startswith("gpt-realtime") or model.startswith("gpt-4o-realtime-preview"):
        return "gpt-realtime-1.5"
    return model


def _is_realtime_model(model: str) -> bool:
    return "realtime" in model
