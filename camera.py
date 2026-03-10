"""Camera entity for Neakasa robot vacuum map."""
from __future__ import annotations

import io
import struct
import zlib

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, _LOGGER
from .coordinator import NeakasaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Neakasa camera from config entry."""
    coordinator: NeakasaCoordinator = hass.data[DOMAIN][config_entry.entry_id].coordinator
    device_info = DeviceInfo(
        name=coordinator.devicename,
        manufacturer="Neakasa",
        identifiers={(DOMAIN, coordinator.deviceid)},
    )
    async_add_entities([NeakasaMapCamera(coordinator, device_info)])


# ---------------------------------------------------------------------------
# Colors (R, G, B) – matching the official Neakasa app style
# ---------------------------------------------------------------------------
_COLOR_UNKNOWN = (212, 201, 180)  # light beige  – unexplored area
_COLOR_FREE    = (210, 180, 140)  # tan/beige    – free / cleaned area
_COLOR_WALL    = (61,  43,  31)   # dark brown   – walls / obstacles
_COLOR_PATH    = (255, 255, 255)  # white        – cleaning path
_COLOR_CHARGE  = (80,  180, 80)   # green        – charging station

# Map pixel values returned by the device
_PIXEL_FREE    = 127
_PIXEL_WALL    = 255

# Output scale: each grid pixel → (SCALE_X, SCALE_Y) output pixels.
# SCALE_X < SCALE_Y because the grid pixels are physically taller than wide
# (empirically 3:4, matching the app's map aspect ratio).
_SCALE_X = 3
_SCALE_Y = 4


class NeakasaMapCamera(CoordinatorEntity[NeakasaCoordinator], Camera):
    """Camera entity that renders the robot vacuum map as a PNG."""

    _attr_has_entity_name = True
    _attr_name = "Karte"
    _attr_content_type = "image/png"
    _attr_is_streaming = False

    def __init__(self, coordinator: NeakasaCoordinator, device_info: DeviceInfo) -> None:
        """Initialize the camera."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.device_info = device_info
        self._attr_unique_id = f"{coordinator.deviceid}_map"
        self._cached_image: bytes | None = None
        self._cached_map_id: str | None = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current map as PNG bytes."""
        map_data = self.coordinator.data.map_data if self.coordinator.data else None
        if not map_data:
            return self._render_placeholder()

        map_id = str(map_data.get("map_id", ""))

        # Return cached image if map hasn't changed
        if self._cached_image and self._cached_map_id == map_id:
            return self._cached_image

        try:
            png = self._render_map(map_data)
            self._cached_image = png
            self._cached_map_id = map_id
            return png
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to render map: %s", err)
            return self._render_placeholder()

    def _render_map(self, map_data: dict) -> bytes:
        """Decode and render the map into a PNG."""
        import base64  # noqa: PLC0415

        map_b64: str    = map_data.get("map_base64", "")
        grid_w: int     = map_data.get("width", 0)
        grid_h: int     = map_data.get("height", 0)
        x_min_m: float  = map_data.get("x_min", 0.0) or 0.0
        y_min_m: float  = map_data.get("y_min", 0.0) or 0.0
        res: float      = map_data.get("resolution", 0.05) or 0.05
        charge_pos      = map_data.get("charge_pos") or []
        path_points     = map_data.get("path_points") or []

        if not map_b64 or not grid_w or not grid_h:
            return self._render_placeholder()

        grid = _lz4_decompress(base64.b64decode(map_b64), grid_w * grid_h)
        if len(grid) < grid_w * grid_h:
            _LOGGER.warning(
                "Map decompression incomplete: got %d of %d bytes",
                len(grid), grid_w * grid_h,
            )
            return self._render_placeholder()

        out_w = grid_w * _SCALE_X
        out_h = grid_h * _SCALE_Y

        # --- Base map ---
        scanlines = bytearray()
        for oy in range(out_h):
            src_y = grid_h - 1 - int(oy * grid_h / out_h)  # flip Y (origin = bottom-left)
            scanlines.append(0)  # PNG filter byte (None filter)
            for ox in range(out_w):
                src_x = int(ox * grid_w / out_w)
                v = grid[src_y * grid_w + src_x]
                if v == _PIXEL_WALL:
                    scanlines.extend(_COLOR_WALL)
                elif v == _PIXEL_FREE:
                    scanlines.extend(_COLOR_FREE)
                else:
                    scanlines.extend(_COLOR_UNKNOWN)

        def mm_to_out(x_mm: float, y_mm: float) -> tuple[int, int]:
            """Convert world mm coordinates to output pixel coordinates."""
            gx = (x_mm / 1000.0 - x_min_m) / res
            gy = (y_mm / 1000.0 - y_min_m) / res
            ox = int(gx * out_w / grid_w)
            oy = out_h - 1 - int(gy * out_h / grid_h)
            return ox, oy

        # --- Cleaning path (white line) ---
        if len(path_points) >= 2:
            prev: tuple[int, int] | None = None
            for x_mm, y_mm in path_points:
                px, py = mm_to_out(x_mm, y_mm)
                if prev is not None:
                    _draw_line(scanlines, out_w, out_h, prev[0], prev[1], px, py, _COLOR_PATH)
                prev = (px, py)

        # --- Charging station (green circle) ---
        # charge_pos is in millimeters [x_mm, y_mm]
        if len(charge_pos) >= 2:
            cx, cy = mm_to_out(charge_pos[0], charge_pos[1])
            if 0 <= cx < out_w and 0 <= cy < out_h:
                _draw_circle(scanlines, out_w, out_h, cx, cy, radius=5, color=_COLOR_CHARGE)

        return _encode_png(scanlines, out_w, out_h)

    def _render_placeholder(self) -> bytes:
        """Return a small placeholder PNG when no map is available."""
        w, h = 60, 80
        scanlines = bytearray()
        for _ in range(h):
            scanlines.append(0)
            scanlines.extend(_COLOR_UNKNOWN * w)
        return _encode_png(scanlines, w, h)

    @property
    def available(self) -> bool:
        """Return True if coordinator data is fresh."""
        return self.coordinator.last_update_success


# ---------------------------------------------------------------------------
# Pure-Python LZ4 block decompressor (no external dependency)
# ---------------------------------------------------------------------------

def _lz4_decompress(src: bytes, max_out: int) -> bytes:
    """Decompress a raw LZ4 block, stopping at max_out bytes.

    The Neakasa map payload is a raw LZ4 block where the very first byte
    is already an LZ4 token (no size-prefix header to skip).
    """
    dst = bytearray()
    pos = 0
    while pos < len(src) and len(dst) < max_out:
        token = src[pos]; pos += 1

        lit_len = token >> 4
        if lit_len == 15:
            while pos < len(src):
                extra = src[pos]; pos += 1
                lit_len += extra
                if extra != 255:
                    break

        dst.extend(src[pos : pos + min(lit_len, len(src) - pos)])
        pos += lit_len

        if len(dst) >= max_out or pos + 1 >= len(src):
            break

        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        if offset == 0:
            break

        match_len = (token & 0xF) + 4
        if (token & 0xF) == 15:
            while pos < len(src):
                extra = src[pos]; pos += 1
                match_len += extra
                if extra != 255:
                    break

        match_start = len(dst) - offset
        if match_start < 0:
            break
        for i in range(match_len):
            if len(dst) >= max_out:
                break
            dst.append(dst[match_start + i])

    return bytes(dst)


def decode_path(path_raw: bytes) -> list[tuple[int, int]]:
    """Decode a binary HisPath / CurPath blob into absolute (x_mm, y_mm) points.

    Binary layout (little-endian):
      bytes  0– 3  header_size (always 28)
      bytes  4– 7  version
      bytes  8–11  path_id
      bytes 12–15  uncompressed_size  (num_points * 4 bytes)
      bytes 16–27  reserved
      bytes 28–    LZ4-compressed int16 delta pairs (dx_mm, dy_mm)

    Each point is the cumulative sum of all preceding deltas from (0, 0).
    """
    if len(path_raw) < 28:
        return []
    header_size       = struct.unpack_from("<I", path_raw, 0)[0]
    uncompressed_size = struct.unpack_from("<I", path_raw, 12)[0]
    compressed_block  = path_raw[header_size:]
    raw = _lz4_decompress(compressed_block, uncompressed_size)

    points: list[tuple[int, int]] = []
    cx = cy = 0
    for i in range(0, len(raw) - 3, 4):
        dx = struct.unpack_from("<h", raw, i)[0]
        dy = struct.unpack_from("<h", raw, i + 2)[0]
        cx += dx
        cy += dy
        points.append((cx, cy))
    return points


# ---------------------------------------------------------------------------
# PNG helpers (no Pillow / numpy dependency)
# ---------------------------------------------------------------------------

def _png_chunk(name: bytes, data: bytes) -> bytes:
    payload = name + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    )


def _encode_png(scanlines: bytearray, width: int, height: int) -> bytes:
    buf = io.BytesIO()
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    buf.write(_png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 6)))
    buf.write(_png_chunk(b"IEND", b""))
    return buf.getvalue()


def _set_pixel(
    sl: bytearray, w: int, h: int, x: int, y: int, color: tuple[int, int, int]
) -> None:
    if 0 <= x < w and 0 <= y < h:
        i = y * (1 + w * 3) + 1 + x * 3
        sl[i] = color[0]; sl[i + 1] = color[1]; sl[i + 2] = color[2]


def _draw_line(
    sl: bytearray, w: int, h: int,
    x0: int, y0: int, x1: int, y1: int,
    color: tuple[int, int, int],
) -> None:
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        _set_pixel(sl, w, h, int(x0 + (x1 - x0) * i / steps), int(y0 + (y1 - y0) * i / steps), color)


def _draw_circle(
    sl: bytearray, w: int, h: int,
    cx: int, cy: int, radius: int,
    color: tuple[int, int, int],
) -> None:
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= radius * radius:
                _set_pixel(sl, w, h, cx + dx, cy + dy, color)