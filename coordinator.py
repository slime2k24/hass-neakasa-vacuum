from dataclasses import dataclass, field
from datetime import timedelta
import logging
import json
import base64
from typing import Optional, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_FRIENDLY_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NeakasaAPI, APIAuthError, APIConnectionError
from .value_cacher import ValueCacher
from .const import DOMAIN, _LOGGER

# WorkMode Mapping (Neabot N2 Lite)
WORK_MODE_MAP = {
    0:  "idle",
    1:  "cleaning",
    2:  "returning",
    3:  "charging",
    4:  "paused",
    5:  "error",
    14: "standby",
    15: "manual",
}

@dataclass
class NeakasaRobotData:
    """Class to hold robot vacuum api data."""

    # Status
    work_mode: int = 0                  # WorkMode: 0=idle,1=cleaning,2=returning,3=charging,...
    work_mode_name: str = "unknown"     # Human readable work mode
    is_cleaning: bool = False
    is_charging: bool = False
    is_paused: bool = False

    # Settings
    pause_switch: int = 0               # PauseSwitch
    led_switch: int = 1                 # LedSwitch
    volume: int = 50                    # Vol
    wind_power: int = 1                 # WindPower: 0=quiet,1=standard,2=strong,3=max

    # Network
    wifi_rssi: int = 0                  # WiFI_RSSI
    wifi_ip: str = ""                   # WifiIp
    wifi_band: str = ""                 # WIFI_Band
    mac_address: str = ""               # MACAddress

    # Device info
    nickname: str = ""                  # Nickname
    mcu_version: str = ""               # McuVersion

    # Map data (raw base64 encoded map from DevMapSend)
    map_data: Optional[dict] = None     # Parsed DevMapSend JSON

    # Extra properties (battery etc. – only available while cleaning)
    battery: Optional[int] = None       # Battery (if available)
    clean_time: Optional[int] = None    # Clean time in seconds (if available)
    clean_area: Optional[float] = None  # Clean area in m² (if available)
    error_code: Optional[int] = None    # Error code (if available)


class NeakasaCoordinator(DataUpdateCoordinator):
    """Coordinator for Neakasa Robot Vacuum."""

    data: NeakasaRobotData

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""

        self.deviceid = config_entry.data[CONF_DEVICE_ID]
        self.devicename = config_entry.data[CONF_FRIENDLY_NAME]
        self.username = config_entry.data[CONF_USERNAME]
        self.password = config_entry.data[CONF_PASSWORD]

        self._deviceName = None
        self._devicePropertiesCache = ValueCacher(
            refresh_after=timedelta(seconds=0),
            discard_after=timedelta(minutes=30)
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=30),
        )

        self.api = None

    async def setProperty(self, key: str, value: Any):
        from . import get_shared_api
        api = await get_shared_api(self.hass, self.username, self.password)
        await api.setDeviceProperties(self.deviceid, {key: value})
        setattr(self.data, key, value)
        self.async_set_updated_data(self.data)

    async def invokeService(self, service: str):
        from . import get_shared_api
        api = await get_shared_api(self.hass, self.username, self.password)
        match service:
            case 'start':
                # Pass the current map_id if we have one
                map_id = 0
                if self.data and self.data.map_data:
                    map_id = self.data.map_data.get('map_id') or 0
                return await api.startCleaning(self.deviceid, map_id=map_id)
            case 'return':
                return await api.returnToBase(self.deviceid)
            case 'pause':
                return await api.pauseCleaning(self.deviceid)
            case 'resume':
                return await api.resumeCleaning(self.deviceid)
        raise Exception(f'Unknown service: {service}')

    async def _getDeviceProperties(self):
        async def fetch():
            from . import get_shared_api
            api = await get_shared_api(self.hass, self.username, self.password)
            return await api.getDeviceProperties(self.deviceid)
        return await self._devicePropertiesCache.get_or_update(fetch)

    def _parse_map_data(self, dev_map_send: list) -> Optional[dict]:
        """Parse the DevMapSend property to extract map metadata."""
        try:
            if not dev_map_send or len(dev_map_send) == 0:
                return None
            raw = dev_map_send[0]
            parsed = json.loads(raw)
            data = parsed.get('data', {})
            return {
                'map_id': data.get('mapId'),
                'width': data.get('width'),
                'height': data.get('height'),
                'resolution': data.get('resolution'),
                'x_min': data.get('x_min'),
                'y_min': data.get('y_min'),
                'charge_pos': data.get('chargeHandlePos'),
                'charge_state': data.get('chargeHandleState'),
                'map_base64': data.get('map'),   # raw lz4+base64 map
                'area': data.get('area', []),
            }
        except Exception as e:
            _LOGGER.debug("Could not parse DevMapSend: %s", e)
            return None

    async def async_update_data(self) -> NeakasaRobotData:
        """Fetch and parse robot vacuum data."""
        try:
            from . import get_shared_api
            api = await get_shared_api(self.hass, self.username, self.password)

            raw = await self._getDeviceProperties()
            _LOGGER.debug("Raw device properties: %s", raw)

            def val(key, default=None):
                """Safely get value from property dict."""
                entry = raw.get(key)
                if entry is None:
                    return default
                return entry.get('value', default)

            work_mode = val('WorkMode', 0)
            work_mode_name = WORK_MODE_MAP.get(work_mode, f"unknown ({work_mode})")

            # Parse map
            map_data = self._parse_map_data(val('DevMapSend', []))

            return NeakasaRobotData(
                work_mode=work_mode,
                work_mode_name=work_mode_name,
                is_cleaning=work_mode == 1,
                is_charging=work_mode == 3,
                is_paused=val('PauseSwitch', 0) == 1,

                pause_switch=val('PauseSwitch', 0),
                led_switch=val('LedSwitch', 1),
                volume=val('Vol', 50),

                wifi_rssi=val('WiFI_RSSI', 0),
                wifi_ip=val('WifiIp', ''),
                wifi_band=val('WIFI_Band', ''),
                mac_address=val('MACAddress', ''),

                nickname=val('Nickname', ''),
                mcu_version=val('McuVersion', ''),

                map_data=map_data,

                # Optional properties (only present while cleaning)
                wind_power=val('WindPower', 1),

                battery=val('Battery'),
                clean_time=val('CleanTime'),
                clean_area=val('CleanArea'),
                error_code=val('ErrorCode'),
            )

        except APIAuthError as err:
            _LOGGER.warning("Auth error for %s, reconnecting: %s", self.devicename, err)
            try:
                from . import force_reconnect_api
                api = await force_reconnect_api(self.hass, self.username, self.password)
                _LOGGER.info("Reconnected API for %s", self.devicename)
                # Retry once after reconnect
                return await self.async_update_data()
            except Exception as reconnect_err:
                _LOGGER.error("Reconnect failed for %s: %s", self.devicename, reconnect_err)
                raise UpdateFailed(f"Auth failed and reconnect failed: {err}") from err

        except APIConnectionError as err:
            if "identityId is blank" in str(err):
                _LOGGER.debug("IdentityId error for %s, reconnecting", self.devicename)
                try:
                    from . import clear_shared_api, force_reconnect_api
                    clear_shared_api(self.username, self.password)
                    await force_reconnect_api(self.hass, self.username, self.password)
                    return await self.async_update_data()
                except Exception as reconnect_err:
                    _LOGGER.error("Reconnect failed: %s", reconnect_err)
                    raise UpdateFailed(f"IdentityId error and reconnect failed: {err}") from err
            else:
                _LOGGER.error("API connection error for %s: %s", self.devicename, err)
                raise UpdateFailed(err) from err