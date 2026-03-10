from dataclasses import dataclass, field
from datetime import timedelta
import logging
import json
import base64
import struct
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
    work_mode: int = 0
    work_mode_name: str = "unknown"
    is_cleaning: bool = False
    is_charging: bool = False
    is_paused: bool = False

    # Settings
    pause_switch: int = 0
    led_switch: int = 1
    volume: int = 50
    wind_power: int = 1             # WindPower: 0=quiet,1=standard,2=strong,3=max

    # Network
    wifi_rssi: int = 0
    wifi_ip: str = ""
    wifi_band: str = ""
    mac_address: str = ""

    # Device info
    nickname: str = ""
    mcu_version: str = ""

    # Map data (parsed DevMapSend + decoded path)
    map_data: Optional[dict] = None

    # Optional properties (only available while cleaning)
    battery: Optional[int] = None
    clean_time: Optional[int] = None
    clean_area: Optional[float] = None
    error_code: Optional[int] = None


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

    # Mapping from API key (PascalCase) to dataclass field name (snake_case)
    _API_KEY_TO_FIELD = {
        "WindPower":   "wind_power",
        "LedSwitch":   "led_switch",
        "PauseSwitch": "pause_switch",
        "Vol":         "volume",
    }

    async def setProperty(self, key: str, value: Any):
        from . import get_shared_api
        api = await get_shared_api(self.hass, self.username, self.password)
        await api.setDeviceProperties(self.deviceid, {key: value})
        # Map to correct dataclass field name and update immediately
        field = self._API_KEY_TO_FIELD.get(key, key)
        setattr(self.data, field, value)
        self.async_set_updated_data(self.data)

    async def invokeService(self, service: str):
        from . import get_shared_api
        api = await get_shared_api(self.hass, self.username, self.password)
        match service:
            case 'start':
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

    def _decode_path(self, path_raw: bytes) -> list:
        """Decode a binary HisPath/CurPath blob into absolute (x_mm, y_mm) points.

        Binary layout (little-endian):
          bytes  0– 3  header_size (always 28)
          bytes  4– 7  version
          bytes  8–11  path_id
          bytes 12–15  uncompressed_size (num_points * 4 bytes)
          bytes 16–27  reserved
          bytes 28–    LZ4-compressed int16 delta pairs (dx_mm, dy_mm)
        """
        if len(path_raw) < 28:
            return []
        try:
            header_size       = struct.unpack_from("<I", path_raw, 0)[0]
            uncompressed_size = struct.unpack_from("<I", path_raw, 12)[0]
            compressed_block  = path_raw[header_size:]

            from .camera import _lz4_decompress
            raw = _lz4_decompress(compressed_block, uncompressed_size)

            points = []
            cx = cy = 0
            for i in range(0, len(raw) - 3, 4):
                dx = struct.unpack_from("<h", raw, i)[0]
                dy = struct.unpack_from("<h", raw, i + 2)[0]
                cx += dx; cy += dy
                points.append((cx, cy))
            return points
        except Exception as e:
            _LOGGER.debug("Could not decode path: %s", e)
            return []

    def _parse_map_data(self, dev_map_send: list, his_path: list) -> Optional[dict]:
        """Parse DevMapSend and HisPath into a unified map_data dict."""
        try:
            if not dev_map_send or len(dev_map_send) == 0:
                return None
            raw = dev_map_send[0]
            parsed = json.loads(raw)
            data = parsed.get('data', {})

            # Decode cleaning path from HisPath
            path_points = []
            if his_path and len(his_path) > 0:
                try:
                    path_raw = base64.b64decode(his_path[0])
                    path_points = self._decode_path(path_raw)
                except Exception as e:
                    _LOGGER.debug("Could not decode HisPath: %s", e)

            return {
                'map_id':       data.get('mapId'),
                'width':        data.get('width'),
                'height':       data.get('height'),
                'resolution':   data.get('resolution'),
                'x_min':        data.get('x_min'),
                'y_min':        data.get('y_min'),
                'charge_pos':   data.get('chargeHandlePos'),
                'charge_state': data.get('chargeHandleState'),
                'map_base64':   data.get('map'),
                'area':         data.get('area', []),
                'path_points':  path_points,
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
                entry = raw.get(key)
                if entry is None:
                    return default
                return entry.get('value', default)

            work_mode = val('WorkMode', 0)
            work_mode_name = WORK_MODE_MAP.get(work_mode, f"unknown ({work_mode})")

            # BatteryState is a float (e.g. 100.0) – convert to int
            battery_raw = val('BatteryState')
            battery = int(battery_raw) if battery_raw is not None else None

            map_data = self._parse_map_data(
                val('DevMapSend', []),
                val('HisPath', []),
            )

            return NeakasaRobotData(
                work_mode=work_mode,
                work_mode_name=work_mode_name,
                is_cleaning=work_mode == 1,
                is_charging=work_mode == 3,
                is_paused=val('PauseSwitch', 0) == 1,

                pause_switch=val('PauseSwitch', 0),
                led_switch=val('LedSwitch', 1),
                volume=val('Vol', 50),
                wind_power=val('WindPower', 1),

                wifi_rssi=val('WiFI_RSSI', 0),
                wifi_ip=val('WifiIp', ''),
                wifi_band=val('WIFI_Band', ''),
                mac_address=val('MACAddress', ''),

                nickname=val('Nickname', ''),
                mcu_version=val('McuVersion', ''),

                map_data=map_data,

                battery=battery,
                clean_time=val('CleanTime'),
                clean_area=val('CleanArea'),
                error_code=val('ErrorCode'),
            )

        except APIAuthError as err:
            _LOGGER.warning("Auth error for %s, reconnecting: %s", self.devicename, err)
            try:
                from . import force_reconnect_api
                await force_reconnect_api(self.hass, self.username, self.password)
                _LOGGER.info("Reconnected API for %s", self.devicename)
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