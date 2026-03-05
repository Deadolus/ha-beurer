import logging
import voluptuous as vol
from typing import Any, Optional, Tuple

from .beurer import BeurerInstance
from .const import DOMAIN

from homeassistant.const import CONF_MAC
import homeassistant.helpers.config_validation as cv
from homeassistant.components.light import (ColorMode, PLATFORM_SCHEMA,
                                            LightEntity, ATTR_RGB_COLOR, ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_WHITE, LightEntityFeature)
from homeassistant.util.color import (match_max_scale)
from homeassistant.helpers import device_registry
from .const import LOGGER

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_MAC): cv.string
})

async def async_setup_entry(hass, config_entry, async_add_devices):
    LOGGER.debug(f"Setting up device from light")
    instance = hass.data[DOMAIN][config_entry.entry_id]
    async_add_devices([BeurerLight(instance, config_entry.data["name"], config_entry.entry_id)])

class BeurerLight(LightEntity):
    def __init__(self, beurerInstance: BeurerInstance, name: str, entry_id: str) -> None:
        self._instance = beurerInstance
        self._entry_id = entry_id
        self._attr_supported_color_modes = {ColorMode.RGB, ColorMode.WHITE}
        self._color_mode = None
        self._attr_name = name
        self._attr_unique_id = self._instance.mac

    async def async_added_to_hass(self) -> None:
        """Add update callback after being added to hass."""
        self._instance.set_update_callback(self.update_callback)
        await self._instance.update()

    def update_callback(self) -> None:
        """Schedule a state update."""
        #self.async_schedule_update_ha_state(False)
        self.schedule_update_ha_state(False)

    @property
    def available(self):
        return self._instance.is_on != None

    #We handle update triggers manually, do not poll
    @property
    def should_poll(self) -> Optional[bool]:
        return False

    @property
    def brightness(self):
        if self._instance.color_mode == ColorMode.WHITE:
            return self._instance.white_brightness
        else:
            return self._instance.color_brightness
        return None

    @property
    def is_on(self) -> Optional[bool]:
        return self._instance.is_on

    @property
    # RGB color/brightness based on https://github.com/home-assistant/core/issues/51175
    def rgb_color(self):
        if self._instance.rgb_color:
            return match_max_scale((255,), self._instance.rgb_color)
        return None

    @property
    def effect(self):
        if self._instance.color_mode == ColorMode.WHITE:
            return "Off"
        else:
            return self._instance.effect

    @property
    def effect_list(self):
        return self._instance.supported_effects

    @property
    def supported_features(self):
        return LightEntityFeature.EFFECT

    @property
    def color_mode(self):
        return self._instance.color_mode

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, self._instance.mac)
            },
            "name": self.name,
            "connections": {(device_registry.CONNECTION_NETWORK_MAC, self._instance.mac)}
        }

    def _transform_color_brightness(self, color: Tuple[int, int, int], set_brightness: int):
        rgb = match_max_scale((255,), color)
        res = tuple(color * set_brightness // 255 for color in rgb)
        return res

    async def async_turn_on(self, **kwargs: Any) -> None:
        LOGGER.debug(f"Turning light on with args: {kwargs}")
        #if not self.is_on:
        #    await self._instance.turn_on()
        if len(kwargs) == 0:
            await self._instance.turn_on()

        if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS]:
            await self._instance.set_white(kwargs[ATTR_BRIGHTNESS])

        if ATTR_RGB_COLOR in kwargs and kwargs[ATTR_RGB_COLOR]:
            color = kwargs[ATTR_RGB_COLOR]
            await self._instance.set_color(color)

        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT]:
            await self._instance.set_effect(kwargs[ATTR_EFFECT])


    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._instance.turn_off()

    async def async_update(self) -> None:
        await self._instance.update()
