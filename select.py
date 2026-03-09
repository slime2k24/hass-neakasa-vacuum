from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, _LOGGER
from .coordinator import NeakasaCoordinator

WIND_POWER_OPTIONS = ["quiet", "standard", "strong", "max"]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Select entities."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator
    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)}
    )

    async_add_entities([
        NeakasaWindPowerSelect(coordinator, device_info)
    ])


class NeakasaWindPowerSelect(CoordinatorEntity, SelectEntity):

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_options = WIND_POWER_OPTIONS

    def __init__(self, coordinator: NeakasaCoordinator, deviceinfo: DeviceInfo) -> None:
        super().__init__(coordinator)
        self.device_info = deviceinfo
        self.translation_key = "wind_power"
        self._attr_unique_id = f"{coordinator.deviceid}-wind_power"
        self._attr_icon = "mdi:fan"

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        value = self.coordinator.data.wind_power
        if 0 <= value < len(WIND_POWER_OPTIONS):
            return WIND_POWER_OPTIONS[value]
        return WIND_POWER_OPTIONS[1]  # default: standard

    async def async_select_option(self, option: str) -> None:
        if option not in WIND_POWER_OPTIONS:
            _LOGGER.error("Invalid wind power option: %s", option)
            return
        value = WIND_POWER_OPTIONS.index(option)
        await self.coordinator.setProperty("WindPower", value)