"""OpenAI catalog helpers for Realtime Satellite."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp
import yaml

from .const import DEFAULT_OPENAI_MODEL_OPTIONS, DEFAULT_OPENAI_VOICE_OPTIONS

_LOGGER = logging.getLogger(__name__)


async def fetch_openai_catalog(api_key: str | None = None) -> dict[str, list[str]]:
    if not api_key:
        return _fallback_catalog()

    try:
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {api_key}"}) as session:
            async with session.get("https://api.openai.com/v1/models") as response:
                response.raise_for_status()
                payload = await response.json()
    except Exception:
        _LOGGER.exception("Failed to fetch OpenAI model catalog; using fallback list")
        return _fallback_catalog()

    model_ids = []
    for model in payload.get("data", []):
        model_id = str(model.get("id", ""))
        if "realtime" not in model_id:
            continue
        model_ids.append(model_id)

    model_ids = sorted(set(model_ids)) or list(DEFAULT_OPENAI_MODEL_OPTIONS)
    return {
        "openai_model_options": model_ids,
        "openai_voice_options": list(DEFAULT_OPENAI_VOICE_OPTIONS),
    }


def load_openai_api_key(config_dir: str) -> Optional[str]:
    secrets_path = Path(config_dir) / "secrets.yaml"
    if not secrets_path.exists():
        return None
    try:
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    except Exception:
        _LOGGER.exception("Failed to read Home Assistant secrets.yaml for OpenAI API key")
        return None
    if not isinstance(secrets, dict):
        return None
    value = secrets.get("openai_api_key")
    return str(value) if value else None


def _fallback_catalog() -> dict[str, list[str]]:
    return {
        "openai_model_options": list(DEFAULT_OPENAI_MODEL_OPTIONS),
        "openai_voice_options": list(DEFAULT_OPENAI_VOICE_OPTIONS),
    }
