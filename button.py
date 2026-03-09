from homeassistant.components.button import ButtonEntity
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
    """Set up the Buttons."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator

    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)}
    )

    buttons = [
        NeakasaButton(coordinator, device_info, translation="start_cleaning",  service="start",  icon="mdi:play"),
        NeakasaButton(coordinator, device_info, translation="pause_cleaning",  service="pause",  icon="mdi:pause"),
        NeakasaButton(coordinator, device_info, translation="resume_cleaning", service="resume", icon="mdi:play-pause"),
        NeakasaButton(coordinator, device_info, translation="return_to_base",  service="return", icon="mdi:home-import-outline"),
    ]

    async_add_entities(buttons)


class NeakasaButton(CoordinatorEntity, ButtonEntity):

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NeakasaCoordinator,
        deviceinfo: DeviceInfo,
        translation: str,
        service: str,
        icon: str = None,
        visible: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self.device_info = deviceinfo
        self.service_key = service
        self.translation_key = translation
        self.entity_registry_enabled_default = visible
        self._attr_unique_id = f"{coordinator.deviceid}-{translation}"
        if icon is not None:
            self._attr_icon = icon

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_press(self) -> None:
        await self.coordinator.invokeService(self.service_key)