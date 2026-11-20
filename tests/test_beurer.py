"""Consumer-visible regressions, with simulated TL100 GATT replies and real HA APIs."""

import inspect
from types import MappingProxyType, SimpleNamespace

from bleak import BleakError
from bleak.backends.device import BLEDevice
import pytest

from homeassistant.config_entries import ConfigEntry, ConfigEntryState, SOURCE_USER
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

MAC = "AA:BB:CC:DD:EE:FF"


def make_entry(hass, address=MAC, state=ConfigEntryState.NOT_LOADED):
    entry = ConfigEntry(
        domain="beurer", title="Therapy lamp", data={CONF_MAC: address, CONF_NAME: "Therapy lamp"},
        options={}, source=SOURCE_USER, unique_id=address.lower(), version=1, minor_version=1,
        discovery_keys=MappingProxyType({}), subentries_data=None, state=state,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.fixture
def lamp(hass, monkeypatch):
    """Simulate only the external GATT boundary, not integration or HA behavior."""
    from custom_components.beurer import beurer

    model = SimpleNamespace(
        white_on=False, color_on=True, white_brightness=75, color_brightness=50,
        rgb=(64, 128, 255), effect=0, clients=[], messages=[], omit=set(), fail=False,
        off_reply=False,
    )
    device = BLEDevice(MAC, "TL100", {})
    monkeypatch.setattr(beurer.bluetooth, "async_ble_device_from_address", lambda *a, **k: device)
    monkeypatch.setattr(beurer.bluetooth, "async_last_service_info", lambda *a, **k: None)
    monkeypatch.setattr(beurer.bluetooth, "async_discovered_service_info", lambda *a, **k: [])

    class Client:
        def __init__(self, disconnected_callback):
            self.is_connected = True
            self.disconnected_callback = disconnected_callback
            self.notify = None
            characteristics = {
                uuid: SimpleNamespace(uuid=uuid, properties=properties)
                for uuid, properties in (
                    ("8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3", ["write-without-response"]),
                    ("0734594a-a8e7-4b1a-a6b1-cd5243059a57", ["notify"]),
                )
            }
            self.services = SimpleNamespace(
                characteristics=characteristics, get_characteristic=characteristics.get,
            )

        async def start_notify(self, characteristic, callback):
            assert self.notify is None, "A connection must not subscribe twice"
            self.notify = callback

        async def stop_notify(self, characteristic):
            self.notify = None

        async def disconnect(self):
            self.is_connected = False
            self.disconnected_callback(self)

        async def write_gatt_char(self, characteristic, packet, *, response):
            if model.fail:
                raise BleakError("Radio link lost")
            packet = bytes(packet)
            assert packet[:3] == bytes([0xFE, 0xEF, 0x0A])
            assert packet[-3:] == bytes([0x55, 0x0D, 0x0A])
            message = list(packet[7:-4])
            checksum = len(message) + 2
            for value in message:
                checksum ^= value
            assert packet[-4] == checksum
            model.messages.append(message)
            command = message[0]
            if command == 0x37:
                if message[1] == 1:
                    model.white_on = True
                else:
                    model.color_on = True
                    model.effect = 2  # Hardware defaults to Rainbow on mood-light activation.
            elif command == 0x35:
                if message[1] == 1:
                    model.white_on = False
                else:
                    model.color_on = False
            elif command == 0x31:
                if message[1] == 1:
                    model.white_brightness = message[2]
                else:
                    model.color_brightness = message[2]
            elif command == 0x32:
                model.rgb = tuple(message[1:])
            elif command == 0x34:
                model.effect = message[1]
            elif command == 0x30 and message[1] not in model.omit:
                version = message[1]
                reply = bytearray(17)
                # Some firmware answers with a single "all off" frame instead.
                reply[8] = 255 if model.off_reply and not (model.white_on or model.color_on) else version
                reply[9] = model.white_on if version == 1 else model.color_on
                reply[10] = model.white_brightness if version == 1 else model.color_brightness
                reply[13:16] = bytes(model.rgb)
                reply[16] = model.effect
                result = self.notify(None, reply)
                if inspect.isawaitable(result):
                    await result

    async def establish(client_class, ble_device, name, **kwargs):
        client = Client(kwargs["disconnected_callback"])
        model.clients.append(client)
        return client

    monkeypatch.setattr(beurer, "establish_connection", establish)
    return model


@pytest.mark.asyncio
async def test_rgb_brightness_and_explicit_white(hass, lamp):
    """Dimming RGB must not activate daylight; explicit white must select daylight."""
    from homeassistant.components.light import ColorMode
    from custom_components.beurer import BeurerCoordinator
    from custom_components.beurer.light import BeurerLight

    coordinator = BeurerCoordinator(hass, make_entry(hass, state=ConfigEntryState.SETUP_IN_PROGRESS))
    try:
        await coordinator.async_config_entry_first_refresh()
        entity = BeurerLight(coordinator, "Therapy lamp")
        await entity.async_turn_on(rgb_color=(255, 0, 0), brightness=128)
        assert lamp.rgb == (255, 0, 0)
        assert [0x31, 0x02, 50] in lamp.messages
        assert not any(message[:2] == [0x31, 0x01] for message in lamp.messages)
        assert entity.color_mode == ColorMode.RGB

        await entity.async_turn_on(white=200)
        assert entity.color_mode == ColorMode.WHITE
        assert entity.brightness == int(int(200 / 255 * 100) * 255 / 100)
        assert lamp.white_on and not lamp.color_on
        await entity.async_turn_off()
        assert not lamp.white_on and not lamp.color_on
        assert not entity.is_on
    finally:
        await coordinator.instance.disconnect()
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_reconnect_preserves_state_and_restores_mood_settings(hass, lamp):
    """Radio loss is unavailable, not off; reconnect uses a fresh client."""
    from custom_components.beurer.beurer import BeurerInstance

    instance = BeurerInstance(hass, MAC)
    try:
        await instance.update()
        assert instance.available and instance.is_on
        await lamp.clients[-1].disconnect()
        assert not instance.available
        assert instance.is_on  # The radio link cannot tell us that the lamp switched off.
        await instance.update()
        assert len(lamp.clients) == 2
        assert instance.available and instance.is_on
        await instance.set_effect("Forest")
        await instance.turn_off()
        await instance.turn_on()
        assert instance.effect == "Forest"
        assert instance.rgb_color == (64, 128, 255)
        assert lamp.effect == 9
    finally:
        await instance.disconnect()


@pytest.mark.asyncio
async def test_malformed_notifications_preserve_reported_state(hass, lamp):
    """Short status frames and unknown effects must not break subsequent control."""
    from custom_components.beurer.beurer import BeurerInstance

    instance = BeurerInstance(hass, MAC)
    try:
        await instance.update()
        before = (instance.is_on, instance.rgb_color, instance.color_brightness, instance.effect)
        for version, length in ((1, 9), (1, 10), (2, 9), (2, 15)):
            reply = bytearray(length)
            reply[8] = version
            lamp.clients[-1].notify(None, reply)
        assert (instance.is_on, instance.rgb_color, instance.color_brightness, instance.effect) == before
        lamp.effect = 255
        await instance.update()
        assert instance.available and instance.effect == before[3]
        await instance.set_effect("Forest")
        assert instance.effect == "Forest"
    finally:
        await instance.disconnect()


@pytest.mark.asyncio
async def test_lamp_off_status_reply_completes_setup(hass, lamp):
    """An all-off reply must count as status, or setup times out with the lamp off."""
    from custom_components.beurer.beurer import BeurerInstance

    lamp.white_on = lamp.color_on = False
    lamp.off_reply = True
    instance = BeurerInstance(hass, MAC)
    try:
        await instance.update()
        assert instance.available and not instance.is_on
        await instance.turn_on()
        assert lamp.color_on and instance.is_on
    finally:
        await instance.disconnect()


@pytest.mark.asyncio
async def test_status_requires_both_replies(hass, lamp, monkeypatch):
    """An incomplete status must not make setup succeed with fabricated state."""
    from custom_components.beurer import BeurerCoordinator
    from custom_components.beurer import beurer

    coordinator = BeurerCoordinator(hass, make_entry(hass, state=ConfigEntryState.SETUP_IN_PROGRESS))
    lamp.omit.add(2)
    monkeypatch.setattr(beurer, "STATUS_TIMEOUT", 0.4)
    try:
        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_config_entry_first_refresh()
        assert not coordinator.instance.available
    finally:
        await coordinator.instance.disconnect()
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_command_failure_is_reported(hass, lamp):
    """A failed write must fail the HA command, not silently report success."""
    from custom_components.beurer import BeurerCoordinator
    from custom_components.beurer.light import BeurerLight

    coordinator = BeurerCoordinator(hass, make_entry(hass, state=ConfigEntryState.SETUP_IN_PROGRESS))
    try:
        await coordinator.async_config_entry_first_refresh()
        entity = BeurerLight(coordinator, "Therapy lamp")
        lamp.fail = True
        with pytest.raises(HomeAssistantError, match="Radio link lost"):
            await entity.async_turn_off()
        assert not entity.available
    finally:
        lamp.fail = False
        await coordinator.instance.disconnect()
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_manual_flow_validation_and_duplicates(hass, lamp):
    """Bad/unreachable MACs remain in the form; duplicates do not touch the lamp."""
    from custom_components.beurer.config_flow import BeurerFlowHandler

    flow = BeurerFlowHandler()
    flow.hass = hass
    flow.handler = "beurer"
    flow.context = {"source": SOURCE_USER}
    result = await flow.async_step_manual({CONF_MAC: "not a mac"})
    assert result["errors"] == {CONF_MAC: "invalid_mac"}
    assert not lamp.clients
    result = await flow.async_step_manual({CONF_MAC: MAC.lower()})
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_MAC: MAC, CONF_NAME: "Beurer TL100"}
    assert all(message[0] == 0x30 for message in lamp.messages)
    assert not lamp.clients[-1].is_connected
    make_entry(hass)
    count = len(lamp.clients)
    with pytest.raises(AbortFlow) as err:
        await flow.async_step_manual({CONF_MAC: MAC.replace(":", "-")})
    assert err.value.reason == "already_configured"
    assert len(lamp.clients) == count


@pytest.mark.asyncio
async def test_discovery_confirmation_and_connection_error(hass, lamp, monkeypatch):
    """Discovery waits for confirmation; an unreachable light is retryable."""
    from custom_components.beurer.config_flow import BeurerFlowHandler
    from custom_components.beurer import beurer

    flow = BeurerFlowHandler()
    flow.hass = hass
    flow.handler = "beurer"
    flow.context = {"source": "bluetooth"}
    result = await flow.async_step_bluetooth(
        SimpleNamespace(address=MAC, name="TL100 Bedroom", connectable=True)
    )
    assert result["step_id"] == "bluetooth_confirm"
    assert not lamp.clients
    monkeypatch.setattr(beurer.bluetooth, "async_ble_device_from_address", lambda *a, **k: None)
    result = await flow.async_step_bluetooth_confirm({})
    assert result["errors"] == {"base": "cannot_connect"}
    assert not lamp.clients


@pytest.mark.asyncio
async def test_home_assistant_entity_lifecycle(hass, lamp):
    """Preserve legacy IDs and exercise setup, services, availability, and unload."""
    from homeassistant.helpers import entity_registry

    entry = make_entry(hass, address=MAC.lower())
    legacy = entity_registry.async_get(hass).async_get_or_create(
        "light", "beurer", MAC, config_entry=entry, suggested_object_id="bedroom_daylight",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED
    state = hass.states.get(legacy.entity_id)
    assert state.state == "on"
    assert set(state.attributes["supported_color_modes"]) == {"rgb", "white"}
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": state.entity_id, "rgb_color": [0, 255, 0], "brightness": 128},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(state.entity_id)
    assert state.attributes["rgb_color"] == (0, 255, 0)
    assert state.attributes["color_mode"] == "rgb"
    await lamp.clients[-1].disconnect()
    await hass.async_block_till_done()
    assert hass.states.get(state.entity_id).state == "unavailable"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(state.entity_id).state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(not client.is_connected for client in lamp.clients)
    assert hass.states.get(state.entity_id).state == "unavailable"
