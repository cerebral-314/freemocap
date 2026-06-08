import base64
import json
import struct

from freemocap_capture_server.protocol import (
    PacketType,
    avcc_to_annexb,
    extract_packets,
    make_camera_settings,
    make_command,
    merge_legacy_camera_locks,
)


def test_extract_packets_handles_fragmented_stream():
    metadata = {"packet_type": "device_status", "status": {"device_id": "iphone_01"}}
    metadata_bytes = json.dumps(metadata).encode("utf-8")
    payload = b"abc"
    packet = struct.pack(">4sBBHII", b"FMC1", 1, PacketType.DEVICE_STATUS, 0, len(metadata_bytes), len(payload))
    packet += metadata_bytes + payload

    buffer = bytearray(packet[:5])
    assert extract_packets(buffer) == []
    buffer.extend(packet[5:])
    packets = extract_packets(buffer)

    assert len(packets) == 1
    assert packets[0].packet_type == PacketType.DEVICE_STATUS
    assert packets[0].metadata == metadata
    assert packets[0].payload == payload
    assert buffer == bytearray()


def test_avcc_to_annexb_prepends_parameter_sets():
    sps = b"sps"
    pps = b"pps"
    nal = b"frame"
    payload = len(nal).to_bytes(4, "big") + nal

    converted = avcc_to_annexb(
        payload,
        nal_unit_header_length=4,
        parameter_sets_base64=[
            base64.b64encode(sps).decode("ascii"),
            base64.b64encode(pps).decode("ascii"),
        ],
    )

    assert converted == b"\x00\x00\x00\x01sps\x00\x00\x00\x01pps\x00\x00\x00\x01frame"


def test_make_command_omits_none_values():
    command = make_command("ping", request_id="abc", missing=None)
    assert json.loads(command.decode("utf-8")) == {"command": "ping", "request_id": "abc"}


def test_make_camera_settings_builds_protocol_payload():
    settings = make_camera_settings(
        camera_selection="lidar",
        resolution="3840x2160",
        fps=60,
        exposure_mode="locked",
        exposure_bias=-0.3,
        focus_mode="locked",
        white_balance_mode="continuous",
        depth_mode="fast_lidar",
    )

    assert settings == {
        "schema_version": 1,
        "camera_selection": "lidar",
        "resolution": "3840x2160",
        "fps": 60,
        "exposure_mode": "locked",
        "exposure_bias": -0.3,
        "focus_mode": "locked",
        "white_balance_mode": "continuous",
        "depth_mode": "fast_lidar",
    }


def test_legacy_lock_flags_merge_into_camera_settings():
    settings = merge_legacy_camera_locks(
        camera_settings=make_camera_settings(fps=30),
        lock_exposure=True,
        lock_focus=False,
        lock_white_balance=True,
    )

    assert settings["exposure_mode"] == "locked"
    assert settings["focus_mode"] == "continuous"
    assert settings["white_balance_mode"] == "locked"
    assert settings["depth_mode"] == "auto_lidar"
    assert settings["camera_selection"] == "auto_back"
