from __future__ import annotations

import asyncio

import pytest


def test_discover_and_turn_on():
    pytest.importorskip("bleak")

    from beurer import BeurerInstance, discover

    async def run():
        devices = await discover()
        assert devices, "No Beurer devices discovered"

        device = devices[0]
        instance = BeurerInstance(device)
        await instance.connect()

        await instance.turn_on()
        assert instance._device.is_connected

        await instance.disconnect()

    asyncio.run(run())
