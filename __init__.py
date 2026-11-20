"""Set up the Beurer Bluetooth integration."""

from datetime import timedelta

from bleak import BleakError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .beurer import BeurerInstance
from .const import DOMAIN, LOGGER

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.LIGHT]


class BeurerCoordinator(DataUpdateCoordinator[BeurerInstance]):
    """Refresh status and reconnect when the lamp drops its connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(seconds=30),
        )
        self.instance = BeurerInstance(hass, entry.data[CONF_MAC])

    async def _async_update_data(self) -> BeurerInstance:
        try:
            await self.instance.update()
        except (BleakError, TimeoutError) as err:
            raise UpdateFailed(f"Unable to communicate with the Beurer lamp: {err}") from err
        return self.instance

    @callback
    def async_handle_update(self) -> None:
        """Publish notification updates, including connection loss."""
        self.async_set_updated_data(self.instance)


type BeurerConfigEntry = ConfigEntry[BeurerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BeurerConfigEntry) -> None:
    """Connect and obtain status before adding the light."""
    coordinator = BeurerCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        coordinator.instance.set_update_callback(coordinator.async_handle_update)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        coordinator.instance.set_update_callback(None)
        await coordinator.instance.disconnect()
        raise

    async def async_stop(_event: Event) -> None:
        """Release the Bluetooth connection on shutdown."""
        await coordinator.instance.disconnect()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop))


async def async_unload_entry(hass: HomeAssistant, entry: BeurerConfigEntry) -> None:
    """Unload entities before releasing the connection."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    entry.runtime_data.instance.set_update_callback(None)
    await entry.runtime_data.instance.disconnect()
