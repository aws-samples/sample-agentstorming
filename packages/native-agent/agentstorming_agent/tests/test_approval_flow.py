# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Test the agent-side approval-request whisper flow.

Verifies that NativeAgent.request_with_approval:
  - returns the broker's headers when prepare_headers succeeds
  - emits org.agentstorming.approval_request whisper when the broker
    raises BrokerError(message='approval_required'); returns None
  - re-raises other BrokerError variants

This test uses Stripe because a payment API is the clearest example of a
credential worth keeping out of the model context. It is illustrative only: no
cardholder data is involved and nothing here is a PCI-DSS control. A deployer
who follows this pattern to process payment card data takes on PCI-DSS scope for
their own environment — see the note in storm_broker/providers/stripe.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from agentstorming_agent.loop import NativeAgent
from agentstorming_client.broker_client import BrokerError


def _write_persona(tmp_path: Path) -> Path:
    persona_dir = tmp_path / "p"
    persona_dir.mkdir()
    cfg = {
        "name": "tester",
        "display_name": "Tester",
        "model": "scripted/echo",
        "backend": "scripted",
        "room_url": "http://localhost:1",
        "room_id": "00000000-0000-0000-0000-000000000000",
    }
    (persona_dir / "persona.yaml").write_text(yaml.safe_dump(cfg))
    (persona_dir / "persona.md").write_text("Test persona.")
    return persona_dir


def _make_agent(tmp_path: Path) -> NativeAgent:
    persona_dir = _write_persona(tmp_path)
    a = NativeAgent(persona_dir)
    # Stub the StormClient — we only need the methods our code touches.
    a.client = MagicMock()
    a.client.pid = "tester@demo"
    a.client.get_room_state = AsyncMock(return_value={"owner_pid": "owner@demo"})
    a.client.post_approval_request = AsyncMock(return_value={"ok": True})
    return a


@pytest.mark.asyncio
async def test_returns_headers_when_broker_allows(tmp_path: Path):
    a = _make_agent(tmp_path)
    a.broker = MagicMock()
    a.broker.prepare_headers.return_value = {"headers": {"Authorization": "Bearer x"}}
    res = await a.request_with_approval(
        provider="stripe", target="stripe",
        method="POST", url="https://api.stripe.com/v1/charges",
        estimated_cost_usd=1.0,
    )
    assert res == {"headers": {"Authorization": "Bearer x"}}
    a.client.post_approval_request.assert_not_called()


@pytest.mark.asyncio
async def test_emits_whisper_on_approval_required(tmp_path: Path):
    a = _make_agent(tmp_path)
    a.broker = MagicMock()
    a.broker.prepare_headers.side_effect = BrokerError(
        code=-32030, message="approval_required",
        data={"approval_id": "ap-123", "estimated_cost_usd": 50.0},
    )
    res = await a.request_with_approval(
        provider="stripe", target="stripe",
        method="POST", url="https://api.stripe.com/v1/charges",
        estimated_cost_usd=50.0, reason="user requested charge",
    )
    assert res is None
    a.client.post_approval_request.assert_awaited_once()
    kwargs = a.client.post_approval_request.await_args.kwargs
    assert kwargs["approval_id"] == "ap-123"
    assert kwargs["tool"] == "stripe.stripe"
    assert kwargs["target_pid"] == "owner@demo"
    assert kwargs["estimated_cost_usd"] == 50.0


@pytest.mark.asyncio
async def test_reraises_other_broker_errors(tmp_path: Path):
    a = _make_agent(tmp_path)
    a.broker = MagicMock()
    a.broker.prepare_headers.side_effect = BrokerError(
        code=-32030, message="rate_limited", data={},
    )
    with pytest.raises(BrokerError):
        await a.request_with_approval(
            provider="stripe", target="stripe",
            method="POST", url="https://api.stripe.com/v1/charges",
            estimated_cost_usd=1.0,
        )


@pytest.mark.asyncio
async def test_returns_none_when_no_broker(tmp_path: Path):
    a = _make_agent(tmp_path)
    a.broker = None
    res = await a.request_with_approval(
        provider="stripe", target="stripe",
        method="POST", url="https://x", estimated_cost_usd=0.0,
    )
    assert res is None
