"""Run the integration against real Home Assistant APIs without Bluetooth hardware."""

from pathlib import Path

import pytest_asyncio

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry, frame


@pytest_asyncio.fixture
async def hass(tmp_path, monkeypatch):
    """Expose the HACS root as a custom component in a disposable config dir."""
    components = tmp_path / "custom_components"
    components.mkdir()
    (components / "beurer").symlink_to(Path(__file__).resolve().parents[1], target_is_directory=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    monkeypatch.setattr(frame._hass, "hass", instance)
    instance.config_entries = ConfigEntries(instance, {})
    device_registry.async_setup(instance)
    await instance.config_entries.async_initialize()
    await area_registry.async_load(instance, load_empty=True)
    await device_registry.async_load(instance, load_empty=True)
    await entity_registry.async_load(instance, load_empty=True)
    # The transport is simulated; do not start adapters or Supervisor discovery.
    instance.config.components.update({"bluetooth", "bluetooth_adapters"})
    yield instance
    await instance.async_stop(force=True)
    await instance.async_block_till_done()
