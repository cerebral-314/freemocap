import json
import struct
from pathlib import Path

from freemocap_capture_server.protocol import CapturePacket, PacketType
from freemocap_capture_server.recorder import CaptureSessionRecorder


def test_recorder_writes_freemocap_layout(tmp_path: Path):
    recorder = CaptureSessionRecorder(output_root=tmp_path, session_id="session_01")
    recorder.handle_packet(
        CapturePacket(
            packet_type=PacketType.DEVICE_STATUS,
            flags=0,
            metadata={
                "status": {
                    "device_id": "iphone_01",
                    "camera_settings": {
                        "schema_version": 1,
                        "resolution": "1920x1080",
                        "fps": 30,
                        "exposure_mode": "continuous",
                        "exposure_bias": 0.0,
                        "focus_mode": "continuous",
                        "white_balance_mode": "continuous",
                        "depth_mode": "auto_lidar",
                    },
                }
            },
            payload=b"",
        ),
        server_receive_time_ns=900,
    )
    video_metadata = {
        "packet_type": "video_frame",
        "metadata": {
            "session_id": "session_01",
            "device_id": "iphone_01",
            "frame_index": 0,
            "capture_time_ns": 1000,
            "presentation_timestamp": 1.0,
            "width": 1920,
            "height": 1080,
            "orientation": "landscapeRight",
        },
        "is_keyframe": False,
        "h264_nal_unit_header_length": 4,
    }
    nal = b"frame"
    recorder.handle_packet(
        CapturePacket(
            packet_type=PacketType.VIDEO_FRAME,
            flags=0,
            metadata=video_metadata,
            payload=struct.pack(">I", len(nal)) + nal,
        ),
        server_receive_time_ns=1200,
    )
    recorder.finalize()

    root = tmp_path / "session_01"
    assert (root / "synchronized_videos").is_dir()
    assert (root / "output_data" / "raw_data" / "rgbd_frame_manifest.json").is_file()
    assert (root / "raw_streams" / "iphone_01" / "video_annexb.h264").read_bytes() == b"\x00\x00\x00\x01frame"

    manifest = json.loads((root / "output_data" / "raw_data" / "rgbd_frame_manifest.json").read_text())
    assert manifest["devices"]["iphone_01"]["video_frame_count"] == 1
    metadata = json.loads((root / "output_data" / "raw_data" / "device_calibration_metadata.json").read_text())
    assert metadata["iphone_01"]["camera_settings"]["resolution"] == "1920x1080"
