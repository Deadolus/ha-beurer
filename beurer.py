"""Home Assistant Bluetooth transport for the Beurer TL100."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant

from .const import LOGGER

WRITE_CHARACTERISTIC_UUIDS = ["8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"]
READ_CHARACTERISTIC_UUIDS = ["0734594a-a8e7-4b1a-a6b1-cd5243059a57"]
STATUS_TIMEOUT = 10.0


class BeurerInstance:
    """Keep one subscribed connection and serialize complete TL100 operations."""

    def __init__(self, hass: HomeAssistant, mac: str) -> None:
        self._hass = hass
        self._mac = mac.upper() if ":" in mac else mac
        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._write_characteristic: BleakGATTCharacteristic | None = None
        self._read_characteristic: BleakGATTCharacteristic | None = None
        self._notifying = False
        self._shutdown_requested = False
        self._known_status: set[int] = set()
        self._status_event: asyncio.Event | None = None
        self._pending_status: set[int] | None = None
        self._status_error: BleakError | None = None
        self._trigger_update: Callable[[], None] | None = None
        self._light_on = False
        self._color_on = False
        self._rgb_color = (0, 0, 0)
        self._white_brightness = 255
        self._color_brightness = 255
        self._effect = "Off"
        self._mode = ColorMode.RGB
        self._supported_effects = [
            "Off", "Random", "Rainbow", "Rainbow Slow", "Fusion", "Pulse",
            "Wave", "Chill", "Action", "Forest", "Summer",
        ]

    @property
    def mac(self) -> str:
        return self._mac

    @property
    def available(self) -> bool:
        return bool(
            self._client is not None
            and self._client.is_connected
            and not self._shutdown_requested
            and self._known_status == {1, 2}
        )

    @property
    def is_on(self) -> bool:
        return self._light_on or self._color_on

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return self._rgb_color

    @property
    def color_brightness(self) -> int:
        return self._color_brightness

    @property
    def white_brightness(self) -> int:
        return self._white_brightness

    @property
    def effect(self) -> str:
        return self._effect

    @property
    def color_mode(self) -> ColorMode:
        return self._mode

    @property
    def supported_effects(self) -> list[str]:
        return self._supported_effects

    def set_update_callback(self, callback: Callable[[], None] | None) -> None:
        """Set the synchronous, event-loop-only status listener."""
        self._trigger_update = callback

    def _notify_update(self) -> None:
        if self._trigger_update is not None:
            self._trigger_update()

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        async with self._lock:
            try:
                yield
            except BaseException:
                # Preserve the original failure, including cancellation, after
                # awaiting cleanup rather than leaving a stale client behind.
                try:
                    await self._disconnect_locked()
                except Exception:
                    LOGGER.exception("Error cleaning up TL100 %s", self._mac)
                raise

    def _resolve_device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(
            self._hass, self._mac, connectable=True
        )
        if device is None:
            raise BleakError(f"No connectable Bluetooth device found for {self._mac}")
        return device

    async def connect(self) -> None:
        """Establish the connection and its single notification subscription."""
        async with self._operation():
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if (
            self._client is not None
            and self._client.is_connected
            and self._notifying
            and not self._shutdown_requested
        ):
            return

        await self._disconnect_locked()
        self._shutdown_requested = False
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._resolve_device(),
            self._mac,
            disconnected_callback=self._disconnected_callback,
            ble_device_callback=self._resolve_device,
            timeout=20.0,
        )
        await asyncio.sleep(0.1)
        self._check_connected()
        for uuid in WRITE_CHARACTERISTIC_UUIDS:
            if characteristic := self._client.services.get_characteristic(uuid):
                self._write_characteristic = characteristic
                break
        for uuid in READ_CHARACTERISTIC_UUIDS:
            if characteristic := self._client.services.get_characteristic(uuid):
                self._read_characteristic = characteristic
                break
        if self._write_characteristic is None or self._read_characteristic is None:
            raise BleakError(f"TL100 {self._mac} is missing a required characteristic")

        client = self._client
        await client.start_notify(
            self._read_characteristic,
            lambda characteristic, data: self._dispatch_on_loop(
                self._handle_notification, client, data
            ),
        )
        self._check_connected()
        self._notifying = True
        await asyncio.sleep(0.1)
        self._check_connected()

    def _check_connected(self) -> None:
        if (
            self._client is None
            or not self._client.is_connected
            or self._shutdown_requested
        ):
            raise BleakError(f"TL100 {self._mac} is disconnected")

    async def _send_packet_locked(
        self, message: list[int], *, delay: float = 0.1
    ) -> None:
        self._check_connected()
        if self._write_characteristic is None:
            raise BleakError(f"TL100 {self._mac} has no write characteristic")
        length = len(message)
        checksum = length + 2
        for value in message:
            checksum ^= value
        packet = bytes(
            [0xFE, 0xEF, 0x0A, length + 7, 0xAB, 0xAA, length + 2]
            + message
            + [checksum, 0x55, 0x0D, 0x0A]
        )
        LOGGER.debug("Sending TL100 %s: %s", self._mac, packet.hex(" "))
        # The lamp worked with write-without-response, bleak's old default.
        await self._client.write_gatt_char(
            self._write_characteristic,
            packet,
            response="write-without-response" not in self._write_characteristic.properties,
        )
        await asyncio.sleep(delay)

    async def update(self) -> None:
        """Refresh both channels, failing if either complete reply is missing."""
        async with self._operation():
            await self._connect_locked()
            await self._request_status_locked()

    async def _request_status_locked(self) -> None:
        self._status_event = asyncio.Event()
        self._pending_status = {1, 2}
        self._status_error = None
        try:
            async with asyncio.timeout(STATUS_TIMEOUT):
                await self._send_packet_locked([0x30, 0x01], delay=0.2)
                await self._send_packet_locked([0x30, 0x02])
                await self._status_event.wait()
                if self._status_error is not None:
                    raise self._status_error
                self._check_connected()
        except TimeoutError as err:
            missing = sorted(self._pending_status or ())
            raise TimeoutError(
                f"TL100 {self._mac} did not answer status request(s) {missing} "
                f"within {STATUS_TIMEOUT:g} s"
            ) from err
        finally:
            self._status_event = None
            self._pending_status = None
            self._status_error = None

    async def _prepare_locked(self) -> None:
        await self._connect_locked()
        if not self.available:
            await self._request_status_locked()

    @staticmethod
    def _device_brightness(brightness: int) -> int:
        # Keep the lowest Home Assistant brightness values from becoming 0%.
        return max(1, int(brightness * 100 / 255))

    async def _activate_channel_locked(self, mode: ColorMode) -> None:
        """Switch channels without calling another locked public operation."""
        if mode == ColorMode.WHITE:
            if self._color_on:
                await self._send_packet_locked([0x35, 0x02])
            if not self._light_on:
                await self._send_packet_locked([0x37, 0x01], delay=0.2)
        else:
            rgb_color = self._rgb_color
            brightness = self._color_brightness
            effect = self._effect
            if self._light_on:
                await self._send_packet_locked([0x35, 0x01])
            if not self._color_on:
                await self._send_packet_locked([0x37, 0x02], delay=0.2)
                # Enabling mood light defaults to Rainbow. Restore static
                # color and brightness first, then the remembered animation.
                await self._send_packet_locked([0x32, *rgb_color])
                await self._send_packet_locked(
                    [0x31, 0x02, round(brightness * 100 / 255)],
                    delay=0.2,
                )
                await self._send_packet_locked(
                    [0x34, self._supported_effects.index(effect)], delay=0.2
                )
        self._mode = mode

    async def turn_on(self) -> None:
        async with self._operation():
            await self._prepare_locked()
            await self._activate_channel_locked(self._mode)
            await self._request_status_locked()

    async def turn_off(self) -> None:
        async with self._operation():
            await self._prepare_locked()
            await self._send_packet_locked([0x35, 0x01])
            await self._send_packet_locked([0x35, 0x02])
            await self._request_status_locked()

    async def set_color(self, rgb: tuple[int, int, int]) -> None:
        async with self._operation():
            await self._prepare_locked()
            await self._activate_channel_locked(ColorMode.RGB)
            await self._send_packet_locked([0x32, *rgb])
            await self._request_status_locked()

    async def set_color_brightness(self, brightness: int) -> None:
        async with self._operation():
            await self._prepare_locked()
            await self._activate_channel_locked(ColorMode.RGB)
            await self._send_packet_locked(
                [0x31, 0x02, self._device_brightness(brightness)]
            )
            await self._request_status_locked()

    async def set_white(self, intensity: int) -> None:
        async with self._operation():
            await self._prepare_locked()
            await self._activate_channel_locked(ColorMode.WHITE)
            await self._send_packet_locked(
                [0x31, 0x01, self._device_brightness(intensity)], delay=0.2
            )
            await self._request_status_locked()

    async def set_effect(self, effect: str) -> None:
        # list.index deliberately raises ValueError for an unknown effect.
        position = self._supported_effects.index(effect)
        async with self._operation():
            await self._prepare_locked()
            await self._activate_channel_locked(ColorMode.RGB)
            await self._send_packet_locked([0x34, position])
            await self._request_status_locked()

    def _dispatch_on_loop(self, callback: Callable, *args) -> None:
        try:
            on_loop = asyncio.get_running_loop() is self._hass.loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            callback(*args)
        else:
            self._hass.loop.call_soon_threadsafe(callback, *args)

    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        self._dispatch_on_loop(self._handle_disconnect, client)

    def _invalidate_status(self, reason: str) -> None:
        self._known_status.clear()
        self._status_error = BleakError(reason)
        if self._status_event is not None:
            self._status_event.set()

    def _handle_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        self._notifying = False
        self._invalidate_status(f"TL100 {self._mac} disconnected during status refresh")
        # Keep last power/mode/brightness values: unavailability is not off.
        self._notify_update()

    def _handle_notification(
        self, client: BleakClientWithServiceCache, data: bytearray
    ) -> None:
        if client is not self._client or self._shutdown_requested or not client.is_connected:
            return
        LOGGER.debug("Received TL100 %s: %s", self._mac, data.hex(" "))
        if len(data) < 9:
            return
        version = data[8]
        if version == 1:
            if len(data) < 11:
                return
            self._light_on = data[9] == 1
            self._white_brightness = min(255, int(data[10] * 255 / 100))
        elif version == 2:
            # Old firmware handling read the effect only while mood light was on.
            if len(data) < 16:
                return
            self._color_on = data[9] == 1
            self._color_brightness = min(255, int(data[10] * 255 / 100))
            self._rgb_color = (data[13], data[14], data[15])
            if self._color_on and len(data) > 16 and data[16] < len(self._supported_effects):
                self._effect = self._supported_effects[data[16]]
        elif version == 255:
            # The lamp answers status requests with this when both channels are off.
            self._light_on = False
            self._color_on = False
            self._known_status.update((1, 2))
            if self._pending_status is not None:
                self._pending_status.clear()
                self._status_event.set()
            self._notify_update()
            return
        elif version == 0:
            # Do not spawn an unowned task from a notification. An in-flight
            # operation wakes and awaits disconnect; an idle session is marked
            # unusable and cleaned up by disconnect or the next operation.
            self._shutdown_requested = True
            self._invalidate_status(f"TL100 {self._mac} is shutting down")
            self._notify_update()
            return
        else:
            return

        if self._color_on:
            self._mode = ColorMode.RGB
        elif self._light_on:
            self._mode = ColorMode.WHITE
        self._known_status.add(version)
        if self._pending_status is not None:
            self._pending_status.discard(version)
            if not self._pending_status:
                self._status_event.set()
        self._notify_update()

    async def disconnect(self) -> None:
        """Unsubscribe and await disconnect, retaining the last reported state."""
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        client = self._client
        read_characteristic = self._read_characteristic
        self._client = None
        self._write_characteristic = None
        self._read_characteristic = None
        self._notifying = False
        self._invalidate_status(f"TL100 {self._mac} disconnected")
        try:
            if client is not None:
                try:
                    # Also attempt this after a partially failed start_notify.
                    if client.is_connected and read_characteristic is not None:
                        await client.stop_notify(read_characteristic)
                finally:
                    await client.disconnect()
        finally:
            if client is not None:
                self._notify_update()