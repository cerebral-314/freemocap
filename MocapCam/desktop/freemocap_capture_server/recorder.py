from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clock_sync import ClockSyncEstimator, ClockSyncSample
from .protocol import CapturePacket, PacketType, avcc_to_annexb


@dataclass
class DeviceRecorder:
    device_id: str
    root: Path
    video_frame_count: int = 0
    depth_frame_count: int = 0
    status: dict[str, Any] = field(default_factory=dict)
    video_frame_manifest: list[dict[str, Any]] = field(default_factory=list)
    depth_frame_manifest: list[dict[str, Any]] = field(default_factory=list)
    local_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    sync_estimator: ClockSyncEstimator = field(default_factory=ClockSyncEstimator)

    def __post_init__(self) -> None:
        self.raw_device_path.mkdir(parents=True, exist_ok=True)
        self.depth_path.mkdir(parents=True, exist_ok=True)

    @property
    def raw_device_path(self) -> Path:
        return self.root / "raw_streams" / self.device_id

    @property
    def video_h264_path(self) -> Path:
        return self.raw_device_path / "video_annexb.h264"

    @property
    def depth_path(self) -> Path:
        return self.raw_device_path / "depth_uint16_mm"

    def write_video_frame(self, packet: CapturePacket) -> None:
        envelope = packet.metadata
        frame_metadata = envelope["metadata"]
        nal_header_length = int(envelope.get("h264_nal_unit_header_length", 4))
        parameter_sets = envelope.get("h264_parameter_sets_base64") if envelope.get("is_keyframe") else None
        annexb = avcc_to_annexb(packet.payload, nal_header_length, parameter_sets)
        with self.video_h264_path.open("ab") as file:
            file.write(annexb)
        self.video_frame_count += 1
        self.video_frame_manifest.append(
            {
                **frame_metadata,
                "packet_payload_bytes": len(packet.payload),
                "annexb_bytes": len(annexb),
            }
        )

    def write_depth_frame(self, packet: CapturePacket) -> None:
        frame_metadata = packet.metadata["metadata"]
        chunk_id = f"{int(frame_metadata['depth_frame_index']):06d}"
        (self.depth_path / f"{chunk_id}.bin").write_bytes(packet.payload)
        self.depth_frame_count += 1
        self.depth_frame_manifest.append(
            {
                **frame_metadata,
                "depth_chunk_id": chunk_id,
                "payload_bytes": len(packet.payload),
                "depth_file": f"raw_streams/{self.device_id}/depth_uint16_mm/{chunk_id}.bin",
            }
        )

    def write_manifests(self) -> None:
        manifest_path = self.raw_device_path / "frame_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "device_id": self.device_id,
                    "video_h264": str(self.video_h264_path.relative_to(self.root)),
                    "rgb_frames": self.video_frame_manifest,
                    "depth_frames": self.depth_frame_manifest,
                    "local_files": self.local_files,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


class CaptureSessionRecorder:
    def __init__(self, output_root: Path, session_id: str) -> None:
        self.output_root = output_root
        self.session_id = session_id
        self.recording_path = output_root / session_id
        self.synchronized_videos_path = self.recording_path / "synchronized_videos"
        self.raw_data_path = self.recording_path / "output_data" / "raw_data"
        self.devices: dict[str, DeviceRecorder] = {}
        self.events: list[dict[str, Any]] = []
        self.recording_path.mkdir(parents=True, exist_ok=True)
        self.synchronized_videos_path.mkdir(parents=True, exist_ok=True)
        self.raw_data_path.mkdir(parents=True, exist_ok=True)

    def handle_packet(self, packet: CapturePacket, server_receive_time_ns: int) -> None:
        if packet.packet_type == PacketType.DEVICE_STATUS:
            status = packet.metadata["status"]
            device = self.device(status["device_id"])
            device.status = status
        elif packet.packet_type == PacketType.VIDEO_FRAME:
            device = self.device(packet.metadata["metadata"]["device_id"])
            device.write_video_frame(packet)
        elif packet.packet_type == PacketType.DEPTH_FRAME:
            device = self.device(packet.metadata["metadata"]["device_id"])
            device.write_depth_frame(packet)
        elif packet.packet_type == PacketType.RECORDING_EVENT:
            self.events.append(packet.metadata["event"])
        elif packet.packet_type == PacketType.CLOCK_SYNC:
            sync = packet.metadata["sync"]
            device = self.device(sync["device_id"])
            device.sync_estimator.add(
                ClockSyncSample(
                    request_id=sync.get("request_id"),
                    server_time_send_ns=int(sync.get("server_time_send_ns") or 0),
                    device_time_receive_ns=int(sync["device_time_receive_ns"]),
                    device_time_reply_ns=int(sync["device_time_reply_ns"]),
                    server_time_receive_ns=server_receive_time_ns,
                )
            )
        elif packet.packet_type == PacketType.ERROR:
            self.events.append({"event": "device_error", "message": packet.metadata.get("message")})
        elif packet.packet_type == PacketType.LOCAL_FILE_MANIFEST:
            manifest = packet.metadata["manifest"]
            device = self.device(manifest["device_id"])
            for file_entry in manifest.get("files", []):
                device.local_files[file_entry["path"]] = file_entry
        elif packet.packet_type == PacketType.LOCAL_FILE_CHUNK:
            chunk = packet.metadata["chunk"]
            device = self.device(chunk["device_id"])
            recovery_root = self.recording_path / "recovered_local_files" / device.device_id
            recovery_path = safe_recovery_path(recovery_root, chunk["file_path"])
            recovery_path.parent.mkdir(parents=True, exist_ok=True)
            mode = "r+b" if recovery_path.exists() else "wb"
            with recovery_path.open(mode) as file:
                file.seek(int(chunk["offset"]))
                file.write(packet.payload)
            device.local_files[chunk["file_path"]] = {
                **device.local_files.get(chunk["file_path"], {}),
                "recovered_path": str(recovery_path.relative_to(self.recording_path)),
                "recovered_bytes": int(chunk["offset"]) + len(packet.payload),
                "file_size_bytes": int(chunk["file_size_bytes"]),
                "is_complete": bool(chunk["is_final"]),
            }

    def device(self, device_id: str) -> DeviceRecorder:
        safe_device_id = normalize_device_id(device_id)
        if safe_device_id not in self.devices:
            self.devices[safe_device_id] = DeviceRecorder(device_id=safe_device_id, root=self.recording_path)
        return self.devices[safe_device_id]

    def estimated_device_start_time_ns(self, device_id: str, server_start_time_ns: int) -> int:
        return int(server_start_time_ns + self.device(device_id).sync_estimator.offset_ns())

    def finalize(self) -> None:
        for device in self.devices.values():
            device.write_manifests()
            self._try_mux_mp4(device)
        self._write_rgbd_manifest()
        self._write_sync_report()
        self._write_device_metadata()
        self._write_placeholder_calibration_note()

    def _try_mux_mp4(self, device: DeviceRecorder) -> None:
        target = self.synchronized_videos_path / f"{device.device_id}.mp4"
        if not device.video_h264_path.exists():
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        subprocess.run(
            [ffmpeg, "-y", "-f", "h264", "-i", str(device.video_h264_path), "-c", "copy", str(target)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _write_rgbd_manifest(self) -> None:
        manifest = {
            "session_id": self.session_id,
            "devices": {
                device_id: {
                    "status": device.status,
                    "video_frame_count": device.video_frame_count,
                    "depth_frame_count": device.depth_frame_count,
                    "raw_manifest": f"raw_streams/{device_id}/frame_manifest.json",
                    "synchronized_video": f"synchronized_videos/{device_id}.mp4",
                }
                for device_id, device in sorted(self.devices.items())
            },
            "recording_events": self.events,
        }
        (self.raw_data_path / "rgbd_frame_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_sync_report(self) -> None:
        report = {
            "session_id": self.session_id,
            "devices": {
                device_id: {
                    **device.sync_estimator.report(),
                    "rgb_frames_received": device.video_frame_count,
                    "depth_frames_received": device.depth_frame_count,
                    "final_alignment_error_ms": None,
                }
                for device_id, device in sorted(self.devices.items())
            },
        }
        (self.raw_data_path / "device_sync_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_device_metadata(self) -> None:
        metadata = {
            device_id: {
                "device_id": device_id,
                "role": "camera",
                "camera_settings": device.status.get("camera_settings"),
                "supported_camera_settings": device.status.get("supported_camera_settings"),
                "intrinsics_source": "apple_avfoundation",
                "recording_mode": "rgbd_local_plus_preview_stream",
                "last_status": device.status,
            }
            for device_id, device in sorted(self.devices.items())
        }
        (self.raw_data_path / "device_calibration_metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_placeholder_calibration_note(self) -> None:
        note_path = self.recording_path / "camera_calibration_data.toml"
        if note_path.exists():
            return
        note_path.write_text(
            "# Placeholder created by MocapCam desktop receiver.\n"
            "# Replace with FreeMoCap ChArUco calibration before reconstruction.\n",
            encoding="utf-8",
        )


def normalize_device_id(device_id: str) -> str:
    normalized = "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in device_id)
    return normalized.strip("_") or "mocapcam"


def safe_recovery_path(root: Path, relative_path: str) -> Path:
    sanitized_parts = [
        part
        for part in Path(relative_path).parts
        if part not in {"", ".", ".."} and not Path(part).is_absolute()
    ]
    return root.joinpath(*sanitized_parts)
