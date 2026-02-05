import asyncio
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .beurer import BeurerInstance, discover, get_device
from .const import DOMAIN, LOGGER

MANUAL_MAC = "manual"


class BeurerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self.mac = None
        self.beurer_instance = None
        self.name = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            if user_input["mac"] == MANUAL_MAC:
                return await self.async_step_manual()

            self.mac = user_input["mac"]
            self.name = user_input["name"]
            await self.async_set_unique_id(format_mac(self.mac))
            self._abort_if_unique_id_configured()
            return await self.async_step_validate()

        already_configured = self._async_current_ids()
        devices = await discover()
        devices = [device for device in devices if format_mac(device.address) not in already_configured]

        if not devices:
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(
                {
                    vol.Required("mac"): vol.In(
                        {
                            **{device.address: device.name for device in devices},
                            MANUAL_MAC: "Manually add a MAC address",
                        }
                    ),
                    vol.Required("name"): str
                }
            ),
            errors={})

    async def async_step_validate(self, user_input: "dict[str, Any] | None" = None):
        if user_input is not None:
            if "flicker" in user_input:
                if user_input["flicker"]:
                    return self.async_create_entry(title=self.name, data={CONF_MAC: self.mac, "name": self.name})
                return self.async_abort(reason="cannot_validate")

            if "retry" in user_input and not user_input["retry"]:
                return self.async_abort(reason="cannot_connect")

        if await self.toggle_light():
            return self.async_show_form(
                step_id="validate", data_schema=vol.Schema(
                    {
                        vol.Required("retry"): bool
                    }
                ), errors={"base": "connect"})

        return self.async_show_form(
            step_id="validate", data_schema=vol.Schema(
                {
                    vol.Required("flicker"): bool
                }
            ), errors={})

    async def async_step_manual(self, user_input: "dict[str, Any] | None" = None):
        if user_input is not None:
            self.mac = user_input["mac"]
            self.name = user_input["name"]
            await self.async_set_unique_id(format_mac(self.mac))
            self._abort_if_unique_id_configured()
            return await self.async_step_validate()

        return self.async_show_form(
            step_id="manual", data_schema=vol.Schema(
                {
                    vol.Required("mac"): str,
                    vol.Required("name"): str
                }
            ), errors={})

    async def toggle_light(self):
        if not self.beurer_instance:
            device = await get_device(self.mac)
            if device is None:
                LOGGER.error("Was not able to find device with mac %s", self.mac)
                return True
            self.beurer_instance = BeurerInstance(device)
        try:
            LOGGER.debug("Going to update from config flow")
            await self.beurer_instance.update()
            LOGGER.debug(f"Finished updating from config flow, light is {self.beurer_instance.is_on}")
            if self.beurer_instance.is_on:
                await self.beurer_instance.turn_off()
                await asyncio.sleep(2)
                await self.beurer_instance.turn_on()
            else:
                await self.beurer_instance.turn_on()
                await asyncio.sleep(2)
                await self.beurer_instance.turn_off()
        except Exception as error:
            LOGGER.error("Error while toggling light: %s", error)
            return True
        finally:
            await self.beurer_instance.disconnect()
