from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfTime,
    EntityCategory,
    UnitOfArea,
)

from .const import DOMAIN, _LOGGER
from .coordinator import NeakasaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator
    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)}
    )

    sensors = [
        # Status
        NeakasaSensor(
            coordinator, device_info,
            translation="work_mode",
            key="work_mode_name",
            icon="mdi:robot-vacuum",
        ),

        # Network / Diagnostic
        NeakasaSensor(
            coordinator, device_info,
            translation="wifi_rssi",
            key="wifi_rssi",
            unit=SIGNAL_STRENGTH_DECIBELS,
            visible=False,
            category=EntityCategory.DIAGNOSTIC,
            icon="mdi:wifi",
        ),
        NeakasaSensor(
            coordinator, device_info,
            translation="wifi_ip",
            key="wifi_ip",
            visible=False,
            category=EntityCategory.DIAGNOSTIC,
            icon="mdi:ip-network",
        ),
        NeakasaSensor(
            coordinator, device_info,
            translation="mcu_version",
            key="mcu_version",
            visible=False,
            category=EntityCategory.DIAGNOSTIC,
            icon="mdi:chip",
        ),

        # Volume
        NeakasaSensor(
            coordinator, device_info,
            translation="volume",
            key="volume",
            unit=PERCENTAGE,
            icon="mdi:volume-high",
        ),

        # Optional sensors (only available while cleaning)
        NeakasaOptionalSensor(
            coordinator, device_info,
            translation="battery",
            key="battery",
            unit=PERCENTAGE,
            device_class=SensorDeviceClass.BATTERY,
            icon="mdi:battery",
        ),
        NeakasaOptionalSensor(
            coordinator, device_info,
            translation="clean_time",
            key="clean_time",
            unit=UnitOfTime.SECONDS,
            icon="mdi:timer",
        ),
        NeakasaOptionalSensor(
            coordinator, device_info,
            translation="clean_area",
            key="clean_area",
            unit=UnitOfArea.SQUARE_METERS,
            icon="mdi:texture-box",
        ),
        NeakasaOptionalSensor(
            coordinator, device_info,
            translation="error_code",
            key="error_code",
            icon="mdi:alert-circle",
            visible=False,
        ),
    ]

    async_add_entities(sensors)


class NeakasaSensor(CoordinatorEntity):

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NeakasaCoordinator,
        deviceinfo: DeviceInfo,
        translation: str,
        key: str,
        unit: str = None,
        icon: str = None,
        visible: bool = True,
        category: str = None,
        device_class: str = None,
    ) -> None:
        super().__init__(coordinator)
        self.device_info = deviceinfo
        self.data_key = key
        self.translation_key = translation
        self.entity_registry_enabled_default = visible
        self._attr_unique_id = f"{coordinator.deviceid}-{translation}"
        if unit is not None:
            self._attr_unit_of_measurement = unit
        if icon is not None:
            self._attr_icon = icon
        if category is not None:
            self._attr_entity_category = category
        if device_class is not None:
            self._attr_device_class = device_class

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def state(self):
        return getattr(self.coordinator.data, self.data_key, None)


class NeakasaOptionalSensor(NeakasaSensor):
    """Sensor that is only available when the value is not None."""

    @property
    def available(self) -> bool:
        return getattr(self.coordinator.data, self.data_key, None) is not None

    @property
    def state(self):
        return getattr(self.coordinator.data, self.data_key, None)