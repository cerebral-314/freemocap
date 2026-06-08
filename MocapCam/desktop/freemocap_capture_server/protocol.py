from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

MAGIC = b"FMC1"
HEADER_SIZE = 16

DEFAULT_CAMERA_SETTINGS: dict[str, Any] = {
    "schema_version": 1,
    "camera_selection": "auto_back",
    "resolution": "1920x1080",
    "fps": 30,
    "exposure_mode": "continuous",
    "exposure_bias": 0.0,
    "focus_mode": "continuous",
    "white_balance_mode": "continuous",
    "depth_mode": "auto_lidar",
}

VALID_RESOLUTIONS = {"1280x720", "1920x1080", "3840x2160"}
VALID_CONTROL_MODES = {"continuous", "locked"}
VALID_CAMERA_SELECTIONS = {
    "auto_back",
    "lidar",
    "triple",
    "dual_wide",
    "dual",
    "wide",
    "ultra_wide",
    "telephoto",
}
VALID_DEPTH_MODES = {"off", "auto_lidar", "fast_lidar", "quality_lidar"}


class PacketType(IntEnum):
    DEVICE_STATUS = 1
    VIDEO_FRAME = 2
    RECORDING_EVENT = 3
    ERROR = 4
    DEPTH_FRAME = 5
    CLOCK_SYNC = 6
    LOCAL_FILE_MANIFEST = 7
    LOCAL_FILE_CHUNK = 8
    CAPTURE_QUALITY = 9


@dataclass(frozen=True)
class CapturePacket:
    packet_type: PacketType
    flags: int
    metadata: dict[str, Any]
    payload: bytes


def make_command(command: str, **values: Any) -> bytes:
    body = {"command": command}
    body.update({key: value for key, value in values.items() if value is not None})
    return json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n"


def make_camera_settings(
    camera_selection: str = DEFAULT_CAMERA_SETTINGS["camera_selection"],
    resolution: str = DEFAULT_CAMERA_SETTINGS["resolution"],
    fps: int = DEFAULT_CAMERA_SETTINGS["fps"],
    exposure_mode: str = DEFAULT_CAMERA_SETTINGS["exposure_mode"],
    exposure_bias: float = DEFAULT_CAMERA_SETTINGS["exposure_bias"],
    focus_mode: str = DEFAULT_CAMERA_SETTINGS["focus_mode"],
    white_balance_mode: str = DEFAULT_CAMERA_SETTINGS["white_balance_mode"],
    depth_mode: str = DEFAULT_CAMERA_SETTINGS["depth_mode"],
) -> dict[str, Any]:
    if camera_selection not in VALID_CAMERA_SELECTIONS:
        raise ValueError(f"Unsupported MocapCam camera selection: {camera_selection}")
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(f"Unsupported MocapCam resolution: {resolution}")
    if exposure_mode not in VALID_CONTROL_MODES:
        raise ValueError(f"Unsupported exposure mode: {exposure_mode}")
    if focus_mode not in VALID_CONTROL_MODES:
        raise ValueError(f"Unsupported focus mode: {focus_mode}")
    if white_balance_mode not in VALID_CONTROL_MODES:
        raise ValueError(f"Unsupported white balance mode: {white_balance_mode}")
    if depth_mode not in VALID_DEPTH_MODES:
        raise ValueError(f"Unsupported MocapCam depth mode: {depth_mode}")

    return {
        **DEFAULT_CAMERA_SETTINGS,
        "camera_selection": camera_selection,
        "resolution": resolution,
        "fps": int(fps),
        "exposure_mode": exposure_mode,
        "exposure_bias": float(exposure_bias),
        "focus_mode": focus_mode,
        "white_balance_mode": white_balance_mode,
        "depth_mode": depth_mode,
    }


def merge_legacy_camera_locks(
    camera_settings: dict[str, Any] | None = None,
    lock_exposure: bool | None = None,
    lock_focus: bool | None = None,
    lock_white_balance: bool | None = None,
) -> dict[str, Any] | None:
    if camera_settings is None and all(value is None for value in (lock_exposure, lock_focus, lock_white_balance)):
        return None

    settings = {**DEFAULT_CAMERA_SETTINGS, **(camera_settings or {})}
    if lock_exposure is not None:
        settings["exposure_mode"] = "locked" if lock_exposure else "continuous"
    if lock_focus is not None:
        settings["focus_mode"] = "locked" if lock_focus else "continuous"
    if lock_white_balance is not None:
        settings["white_balance_mode"] = "locked" if lock_white_balance else "continuous"
    return settings


def extract_packets(buffer: bytearray) -> list[CapturePacket]:
    packets: list[CapturePacket] = []

    while True:
        if len(buffer) < HEADER_SIZE:
            return packets

        magic_index = buffer.find(MAGIC)
        if magic_index < 0:
            del buffer[: max(0, len(buffer) - len(MAGIC) + 1)]
            return packets
        if magic_index > 0:
            del buffer[:magic_index]
            if len(buffer) < HEADER_SIZE:
                return packets

        magic, version, packet_type, flags, metadata_length, payload_length = struct.unpack(
            ">4sBBHII", buffer[:HEADER_SIZE]
        )
        if magic != MAGIC:
            raise ValueError("invalid FMC1 packet magic")
        if version != 1:
            raise ValueError(f"unsupported FMC1 protocol version: {version}")

        packet_length = HEADER_SIZE + metadata_length + payload_length
        if len(buffer) < packet_length:
            return packets

        metadata_start = HEADER_SIZE
        metadata_end = metadata_start + metadata_length
        payload_end = metadata_end + payload_length
        metadata = json.loads(buffer[metadata_start:metadata_end].decode("utf-8"))
        payload = bytes(buffer[metadata_end:payload_end])
        packets.append(
            CapturePacket(
                packet_type=PacketType(packet_type),
                flags=flags,
                metadata=metadata,
                payload=payload,
            )
        )
        del buffer[:packet_length]


def avcc_to_annexb(
    payload: bytes,
    nal_unit_header_length: int = 4,
    parameter_sets_base64: list[str] | None = None,
) -> bytes:
    start_code = b"\x00\x00\x00\x01"
    output = bytearray()

    for parameter_set in parameter_sets_base64 or []:
        output.extend(start_code)
        output.extend(base64.b64decode(parameter_set))

    offset = 0
    while offset + nal_unit_header_length <= len(payload):
        nal_length = int.from_bytes(payload[offset : offset + nal_unit_header_length], "big")
        offset += nal_unit_header_length
        if nal_length <= 0 or offset + nal_length > len(payload):
            break
        output.extend(start_code)
        output.extend(payload[offset : offset + nal_length])
        offset += nal_length

    return bytes(output)
