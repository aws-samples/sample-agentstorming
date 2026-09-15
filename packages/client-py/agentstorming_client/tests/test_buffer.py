# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import asyncio

import pytest

from agentstorming_client.buffer import BufferManager


@pytest.mark.asyncio
async def test_append_and_drain():
    b = BufferManager(capacity=5)
    for i in range(3):
        await b.append({"i": i})
    assert len(await b.snapshot()) == 3
    drained = await b.drain()
    assert len(drained) == 3
    assert await b.size() == 0


@pytest.mark.asyncio
async def test_capacity():
    b = BufferManager(capacity=2)
    for i in range(5):
        await b.append({"i": i})
    snap = await b.snapshot()
    assert len(snap) == 2
    assert snap[-1]["i"] == 4
