from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up the Binary Sensors."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator
    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)}
    )

    sensors = [
        NeakasaBinarySensor(
            coordinator, device_info,
            translation="is_cleaning",
            key="is_cleaning",
            icon="mdi:robot-vacuum",
        ),
        NeakasaBinarySensor(
            coordinator, device_info,
            translation="is_charging",
            key="is_charging",
            icon="mdi:battery-charging",
        ),
        NeakasaBinarySensor(
            coordinator, device_info,
            translation="is_paused",
            key="is_paused",
            icon="mdi:pause-circle",
        ),
        NeakasaBinarySensor(
            coordinator, device_info,
            translation="led_switch",
            key="led_switch",
            icon="mdi:led-on",
        ),
    ]

    async_add_entities(sensors)


class NeakasaBinarySensor(CoordinatorEntity):

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NeakasaCoordinator,
        deviceinfo: DeviceInfo,
        translation: str,
        key: str,
        icon: str = None,
        visible: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self.device_info = deviceinfo
        self.data_key = key
        self.translation_key = translation
        self.entity_registry_enabled_default = visible
        self._attr_unique_id = f"{coordinator.deviceid}-{translation}"
        if icon is not None:
            self._attr_icon = icon

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        value = getattr(self.coordinator.data, self.data_key, False)
        # Handle both bool and int (0/1) values
        if isinstance(value, bool):
            return value
        return value == 1

    @property
    def state(self):
        return STATE_ON if self.is_on else STATE_OFF