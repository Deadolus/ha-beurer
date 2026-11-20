"""Configure Beurer TL100 lights using Home Assistant Bluetooth."""

from typing import Any

from bleak.exc import BleakError
import probatio

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .beurer import BeurerInstance
from .const import DOMAIN, LOGGER

MANUAL_MAC = "manual"
DEFAULT_NAME = "Beurer TL100"
VALID_MAC = cv.matches_regex(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class BeurerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle user setup and Bluetooth discovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow without connecting to a light."""
        self.mac: str | None = None
        self.name = DEFAULT_NAME
        self._discovered_devices: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a connectable light, or enter its address manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MAC] == MANUAL_MAC:
                return await self.async_step_manual()

            discovery = self._discovered_devices[format_mac(user_input[CONF_MAC])]
            self.mac = discovery.address
            self.name = (
                user_input.get(CONF_NAME, "").strip() or discovery.name or DEFAULT_NAME
            )
            if await self._async_validate():
                return self._async_create_entry()
            errors["base"] = "cannot_connect"
        else:
            configured = self._async_current_ids(include_ignore=False)
            self._discovered_devices = {
                format_mac(discovery.address): discovery
                for discovery in bluetooth.async_discovered_service_info(
                    self.hass, connectable=True
                )
                if (discovery.name or "").lower().startswith("tl100")
                and format_mac(discovery.address) not in configured
            }

        if not self._discovered_devices:
            return await self.async_step_manual()

        options = [
            {"value": address, "label": f"{discovery.name} ({discovery.address})"}
            for address, discovery in self._discovered_devices.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_MAC): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                *options,
                                {"value": MANUAL_MAC, "label": MANUAL_MAC},
                            ],
                            translation_key="device",
                        )
                    ),
                    probatio.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate a manually entered MAC without changing the lamp."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                raw_address = cv.string(user_input[CONF_MAC]).strip()
                address = VALID_MAC(format_mac(raw_address)).upper()
            except probatio.Invalid:
                errors[CONF_MAC] = "invalid_mac"
            else:
                self.mac = address
                discovery = bluetooth.async_last_service_info(
                    self.hass, address, connectable=True
                )
                default_name = (
                    discovery.name if discovery and discovery.name else DEFAULT_NAME
                )
                self.name = user_input.get(CONF_NAME, "").strip() or default_name
                if await self._async_validate():
                    return self._async_create_entry()
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_MAC): str,
                    probatio.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Offer a discovered TL100 without connecting before confirmation."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        if not discovery_info.connectable or not (
            discovery_info.name or ""
        ).lower().startswith("tl100"):
            return self.async_abort(reason="not_supported")

        self.mac = discovery_info.address
        self.name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {"name": self.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Bluetooth discovery before reading the light's status."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.name = user_input.get(CONF_NAME, "").strip() or self.name
            if await self._async_validate():
                return self._async_create_entry()
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=probatio.Schema(
                {probatio.Optional(CONF_NAME, default=self.name): str}
            ),
            description_placeholders={"name": self.name},
            errors=errors,
        )

    async def _async_validate(self) -> bool:
        """Reject duplicates before connecting, and release every connection."""
        assert self.mac is not None
        await self.async_set_unique_id(format_mac(self.mac))
        self._abort_if_unique_id_configured()
        instance = BeurerInstance(self.hass, self.mac)
        try:
            try:
                await instance.update()
            finally:
                await instance.disconnect()
        except (BleakError, TimeoutError) as err:
            LOGGER.warning("Unable to validate Beurer light %s: %s", self.mac, err)
            return False
        return True

    def _async_create_entry(self) -> ConfigFlowResult:
        """Keep the version-one stored configuration shape."""
        return self.async_create_entry(
            title=self.name, data={CONF_MAC: self.mac, CONF_NAME: self.name}
        )