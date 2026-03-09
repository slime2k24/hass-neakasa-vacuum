from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    STATE_ON,
    STATE_OFF,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, _LOGGER
from .coordinator import NeakasaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Switches."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator
    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)}
    )

    switches = [
        # LED Switch
        NeakasaSwitch(
            coordinator, device_info,
            translation="led_switch",
            key="led_switch",
            api_key="LedSwitch",
            icon="mdi:led-on",
        ),
        # Pause Switch
        NeakasaSwitch(
            coordinator, device_info,
            translation="pause_switch",
            key="pause_switch",
            api_key="PauseSwitch",
            icon="mdi:pause-circle",
        ),
    ]

    async_add_entities(switches)


class NeakasaSwitch(CoordinatorEntity, SwitchEntity):

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NeakasaCoordinator,
        deviceinfo: DeviceInfo,
        translation: str,
        key: str,
        api_key: str,
        icon: str = None,
        visible: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self.device_info = deviceinfo
        self.data_key = key       # key in NeakasaRobotData dataclass
        self.api_key = api_key    # key used by the Alibaba IoT API
        self.translation_key = translation
        self.entity_registry_enabled_default = visible
        self._attr_unique_id = f"{coordinator.deviceid}-{translation}"
        if icon is not None:
            self._attr_icon = icon

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self.coordinator.setProperty(self.api_key, 1)
        setattr(self.coordinator.data, self.data_key, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.coordinator.setProperty(self.api_key, 0)
        setattr(self.coordinator.data, self.data_key, 0)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        value = getattr(self.coordinator.data, self.data_key, 0)
        if isinstance(value, bool):
            return value
        return value == 1

    @property
    def state(self):
        return STATE_ON if self.is_on else STATE_OFF