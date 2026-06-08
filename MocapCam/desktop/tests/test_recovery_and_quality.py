import json
from pathlib import Path

from freemocap_capture_server.client import packet_device_id
from freemocap_capture_server.protocol import CapturePacket, PacketType
from freemocap_capture_server.quality import score_device_status
from freemocap_capture_server.recorder import CaptureSessionRecorder


def test_local_file_chunk_is_written_safely(tmp_path: Path):
    recorder = CaptureSessionRecorder(output_root=tmp_path, session_id="session_01")
    recorder.handle_packet(
        CapturePacket(
            packet_type=PacketType.LOCAL_FILE_CHUNK,
            flags=0,
            metadata={
                "chunk": {
                    "device_id": "iphone_01",
                    "session_id": "session_01",
                    "file_path": "../sessions/session_01/iphone_01/device_motion.json",
                    "offset": 0,
                    "payload_bytes": 2,
                    "file_size_bytes": 2,
                    "is_final": True,
                }
            },
            payload=b"{}",
        ),
        server_receive_time_ns=0,
    )
    recorder.finalize()

    recovered = tmp_path / "session_01" / "recovered_local_files" / "iphone_01" / "sessions" / "session_01" / "iphone_01" / "device_motion.json"
    assert recovered.read_bytes() == b"{}"


def test_packet_device_id_reads_all_packet_shapes():
    assert packet_device_id(CapturePacket(PacketType.DEVICE_STATUS, 0, {"status": {"device_id": "a"}}, b"")) == "a"
    assert packet_device_id(CapturePacket(PacketType.CLOCK_SYNC, 0, {"sync": {"device_id": "b"}}, b"")) == "b"
    assert packet_device_id(CapturePacket(PacketType.LOCAL_FILE_MANIFEST, 0, {"manifest": {"device_id": "c"}}, b"")) == "c"


def test_quality_score_flags_bad_capture_status():
    quality = score_device_status(
        {
            "fps": 12,
            "depth_supported": True,
            "depth_frames_sent": 0,
            "battery_percent": 10,
            "thermal_state": "serious",
            "dropped_frames": 2,
        },
        {"sample_count": 1, "median_round_trip_latency_ms": 40},
    )
    assert quality.score < 50
    assert quality.warnings
