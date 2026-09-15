# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Test that LiteLLMBackend routes credential fetch through the broker.

Verifies the AT1 wiring: when persona.model_config.broker_provider is
set, the backend asks the broker for the API key (via prepare_credentials)
instead of reading os.environ. Falls back to env when broker is absent
or unset.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from agentstorming_agent.backends import LiteLLMBackend
from agentstorming_agent.config import PersonaConfig, load_persona


def _write_persona(tmp_path: Path, model_config: dict) -> Path:
    persona_dir = tmp_path / "p"
    persona_dir.mkdir()
    (persona_dir / "persona.yaml").write_text(yaml.safe_dump({
        "name": "tester",
        "display_name": "Tester",
        "model": "openai/gpt-4o",
        "backend": "litellm",
        "model_config": model_config,
        "room_url": "http://localhost:1",
        "room_id": "00000000-0000-0000-0000-000000000000",
    }))
    (persona_dir / "persona.md").write_text("Test persona.")
    return persona_dir


@pytest.mark.asyncio
async def test_backend_uses_broker_credential(tmp_path: Path):
    persona_dir = _write_persona(tmp_path, {
        "broker_provider": "openai",
    })
    cfg, _ = load_persona(persona_dir)

    broker = MagicMock()
    broker.prepare_credentials.return_value = {
        "credentials": {"api_key": "sk-from-broker-XYZ"},  # pragma: allowlist secret
    }

    backend = LiteLLMBackend(persona_dir, cfg, broker=broker)

    # Patch litellm.acompletion so we can capture the kwargs.
    fake_resp = {"choices": [{"message": {"content": "ok"}}]}
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return fake_resp

    fake_litellm = MagicMock()
    fake_litellm.acompletion = fake_acompletion
    with patch.dict("sys.modules", {"litellm": fake_litellm}):
        await backend.deliberate(
            events=[{"type": "org.agentstorming.message",
                     "sender": "alice@demo",
                     "payload": {"text": "hi"}}],
            state={},
        )

    broker.prepare_credentials.assert_called_once()
    call_kwargs = broker.prepare_credentials.call_args.kwargs
    assert call_kwargs["provider"] == "openai"
    assert captured["api_key"] == "sk-from-broker-XYZ"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_backend_falls_back_to_env_when_no_broker(tmp_path: Path, monkeypatch):
    persona_dir = _write_persona(tmp_path, {"api_key_env": "SOME_KEY_VAR"})  # pragma: allowlist secret
    cfg, _ = load_persona(persona_dir)
    monkeypatch.setenv("SOME_KEY_VAR", "sk-from-env-ABC")

    backend = LiteLLMBackend(persona_dir, cfg, broker=None)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    fake_litellm = MagicMock()
    fake_litellm.acompletion = fake_acompletion
    with patch.dict("sys.modules", {"litellm": fake_litellm}):
        await backend.deliberate(
            events=[{"type": "org.agentstorming.message",
                     "sender": "alice@demo",
                     "payload": {"text": "hi"}}],
            state={},
        )

    assert captured["api_key"] == "sk-from-env-ABC"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_backend_broker_failure_falls_back_to_env(tmp_path: Path, monkeypatch):
    persona_dir = _write_persona(tmp_path, {
        "broker_provider": "openai",
        "api_key_env": "SOME_KEY_VAR",  # pragma: allowlist secret
    })
    cfg, _ = load_persona(persona_dir)
    monkeypatch.setenv("SOME_KEY_VAR", "sk-from-env-FALLBACK")

    broker = MagicMock()
    broker.prepare_credentials.side_effect = RuntimeError("broker dead")

    backend = LiteLLMBackend(persona_dir, cfg, broker=broker)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    fake_litellm = MagicMock()
    fake_litellm.acompletion = fake_acompletion
    with patch.dict("sys.modules", {"litellm": fake_litellm}):
        await backend.deliberate(
            events=[{"type": "org.agentstorming.message",
                     "sender": "alice@demo",
                     "payload": {"text": "hi"}}],
            state={},
        )

    # Broker failed → falls back to env.
    assert captured["api_key"] == "sk-from-env-FALLBACK"  # pragma: allowlist secret
