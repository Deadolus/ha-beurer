"""Light entity for the Beurer TL100."""

from typing import Any

from bleak import BleakError

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_WHITE,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import match_max_scale

from . import BeurerConfigEntry, BeurerCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BeurerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light backed by the entry's shared connection."""
    async_add_entities([BeurerLight(config_entry.runtime_data, config_entry.data[CONF_NAME])])


class BeurerLight(CoordinatorEntity[BeurerCoordinator], LightEntity):
    """Control the daylight and mood-light channels of a TL100."""

    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.WHITE}
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, coordinator: BeurerCoordinator, name: str) -> None:
        super().__init__(coordinator)
        self._instance = coordinator.instance
        self._attr_name = name
        # Legacy entities used the Bluetooth device's canonical address.
        self._attr_unique_id = self._instance.mac
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._instance.mac)},
            connections={(CONNECTION_BLUETOOTH, self._instance.mac)},
            name=name,
            manufacturer="Beurer",
            model="TL100",
        )

    @property
    def available(self) -> bool:
        return super().available and self._instance.available

    @property
    def brightness(self) -> int | None:
        if self._instance.color_mode == ColorMode.WHITE:
            return self._instance.white_brightness
        return self._instance.color_brightness

    @property
    def is_on(self) -> bool:
        return self._instance.is_on

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return match_max_scale((255,), self._instance.rgb_color)

    @property
    def effect(self) -> str:
        if self._instance.color_mode == ColorMode.WHITE:
            return "Off"
        return self._instance.effect

    @property
    def effect_list(self) -> list[str]:
        return self._instance.supported_effects

    @property
    def color_mode(self) -> ColorMode:
        return self._instance.color_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Select white explicitly; otherwise dim the current/color channel."""
        try:
            if ATTR_WHITE in kwargs:
                await self._instance.set_white(kwargs[ATTR_WHITE])
                return

            if ATTR_RGB_COLOR in kwargs:
                await self._instance.set_color(kwargs[ATTR_RGB_COLOR])
            if ATTR_EFFECT in kwargs:
                await self._instance.set_effect(kwargs[ATTR_EFFECT])
            if ATTR_BRIGHTNESS in kwargs:
                if self._instance.color_mode == ColorMode.WHITE:
                    await self._instance.set_white(kwargs[ATTR_BRIGHTNESS])
                else:
                    await self._instance.set_color_brightness(kwargs[ATTR_BRIGHTNESS])
            elif ATTR_RGB_COLOR not in kwargs and ATTR_EFFECT not in kwargs:
                await self._instance.turn_on()
        except (BleakError, TimeoutError) as err:
            raise HomeAssistantError(f"Unable to control the Beurer lamp: {err}") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch off both channels."""
        try:
            await self._instance.turn_off()
        except (BleakError, TimeoutError) as err:
            raise HomeAssistantError(f"Unable to control the Beurer lamp: {err}") from err
