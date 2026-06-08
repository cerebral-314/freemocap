from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .client import CaptureClient, is_cancelled, monotonic_ns
from .protocol import make_camera_settings, merge_legacy_camera_locks
from .quality import score_device_status
from .recorder import CaptureSessionRecorder


@dataclass(frozen=True)
class DeviceEndpoint:
    host: str
    port: int

    @classmethod
    def parse(cls, value: str) -> "DeviceEndpoint":
        host, port = value.rsplit(":", 1)
        return cls(host=host, port=int(port))


class MultiDeviceCaptureController:
    def __init__(self, endpoints: list[DeviceEndpoint], output_root: Path, session_id: str) -> None:
        self.recorder = CaptureSessionRecorder(output_root=output_root, session_id=session_id)
        self.clients = [
            CaptureClient(host=endpoint.host, port=endpoint.port, recorder=self.recorder)
            for endpoint in endpoints
        ]

    def connect(self) -> None:
        for client in self.clients:
            client.connect()

    def close(self) -> None:
        for client in self.clients:
            client.close()

    def warmup(
        self,
        seconds: float = 3.0,
        depth_preview: bool = True,
        camera_settings: dict[str, object] | None = None,
        lock_exposure: bool | None = None,
        lock_focus: bool | None = None,
        lock_white_balance: bool | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        merged_camera_settings = merge_legacy_camera_locks(
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
        )
        for client in self.clients:
            if merged_camera_settings is not None:
                client.send_command(
                    "set_camera_settings",
                    camera_settings=merged_camera_settings,
                )
            client.send_command("start_preview")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not is_cancelled(cancel_event):
            for client in self.clients:
                client.send_ping()
                client.service_once(timeout_seconds=0.02)
            time.sleep(0.1)

    def arm_all(
        self,
        start_delay_seconds: float = 1.0,
        camera_settings: dict[str, object] | None = None,
        lock_exposure: bool | None = None,
        lock_focus: bool | None = None,
        lock_white_balance: bool | None = None,
    ) -> None:
        merged_camera_settings = merge_legacy_camera_locks(
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
        )
        server_start_time_ns = monotonic_ns() + int(start_delay_seconds * 1_000_000_000)
        for client in self.clients:
            if not client.seen_device_ids:
                client.send_command(
                    "start_recording",
                    session_id=self.recorder.session_id,
                    camera_settings=merged_camera_settings,
                    lock_exposure=lock_exposure,
                    lock_focus=lock_focus,
                    lock_white_balance=lock_white_balance,
                )
                continue
            for device_id in sorted(client.seen_device_ids):
                client.send_command(
                    "arm_recording",
                    session_id=self.recorder.session_id,
                    start_at_server_time_ns=server_start_time_ns,
                    start_at_device_time_ns=self.recorder.estimated_device_start_time_ns(device_id, server_start_time_ns),
                    camera_settings=merged_camera_settings,
                    lock_exposure=lock_exposure,
                    lock_focus=lock_focus,
                    lock_white_balance=lock_white_balance,
                )

    def record(
        self,
        duration_seconds: float,
        depth_preview: bool = True,
        camera_settings: dict[str, object] | None = None,
        lock_exposure: bool | None = None,
        lock_focus: bool | None = None,
        lock_white_balance: bool | None = None,
        recover_local_files: bool = True,
        cancel_event: threading.Event | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> Path:
        self.warmup(
            depth_preview=depth_preview,
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
            cancel_event=cancel_event,
        )
        if is_cancelled(cancel_event):
            self.stop_all()
            self.recorder.finalize()
            return self.recorder.recording_path

        self.print_cockpit(status_callback=status_callback)
        self.arm_all(
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
        )
        deadline = time.monotonic() + duration_seconds
        next_print = time.monotonic() + 1
        while time.monotonic() < deadline and not is_cancelled(cancel_event):
            for client in self.clients:
                client.service_once(timeout_seconds=0.02)
            if time.monotonic() >= next_print:
                self.print_cockpit(status_callback=status_callback)
                next_print = time.monotonic() + 1

        self.stop_all()

        if not recover_local_files:
            self.recorder.finalize()
            return self.recorder.recording_path

        for client in self.clients:
            client.request_local_file_manifest()

        drain_deadline = time.monotonic() + 2
        while time.monotonic() < drain_deadline and not is_cancelled(cancel_event):
            for client in self.clients:
                client.service_once(timeout_seconds=0.02)

        if not is_cancelled(cancel_event):
            self.request_recovery_files(max_file_bytes=8 * 1024 * 1024)

        recovery_deadline = time.monotonic() + 5
        while time.monotonic() < recovery_deadline and not is_cancelled(cancel_event):
            for client in self.clients:
                client.service_once(timeout_seconds=0.02)

        self.recorder.finalize()
        return self.recorder.recording_path

    def stop_all(self) -> None:
        for client in self.clients:
            client.send_command("stop_recording")

    def request_recovery_files(self, max_file_bytes: int) -> None:
        for client in self.clients:
            for device_id in sorted(client.seen_device_ids):
                device = self.recorder.devices.get(device_id)
                if device is None:
                    continue
                for file_path, file_entry in sorted(device.local_files.items()):
                    size = int(file_entry.get("size_bytes") or file_entry.get("file_size_bytes") or 0)
                    if should_recover_file(file_path, size, max_file_bytes):
                        client.request_local_file(file_path=file_path, file_size_bytes=size)

    def print_cockpit(self, status_callback: Callable[[str], None] | None = None) -> None:
        rows = []
        for device_id, device in sorted(self.recorder.devices.items()):
            sync_report = device.sync_estimator.report()
            quality = score_device_status(device.status, sync_report)
            rows.append(
                [
                    device_id,
                    str(device.status.get("recording_active", False)),
                    f"{float(device.status.get('fps') or 0):.1f}",
                    str(device.status.get("depth_supported", False)),
                    f"{sync_report['estimated_offset_ms']:.2f}",
                    str(device.status.get("battery_percent", "--")),
                    str(quality.score),
                    "; ".join(quality.warnings[:2]),
                ]
            )
        if not rows:
            message = "No MocapCam devices have reported status yet."
            if status_callback:
                status_callback(message)
            else:
                print(message)
            return
        message = format_table(["device", "rec", "rgb fps", "depth", "offset ms", "battery", "score", "warnings"], rows)
        if status_callback:
            status_callback(message)
        else:
            print(message)


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(str(value)) for value in [header, *[row[index] for row in rows]])
        for index, header in enumerate(headers)
    ]
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)


def should_recover_file(file_path: str, size_bytes: int, max_file_bytes: int) -> bool:
    if size_bytes <= 0 or size_bytes > max_file_bytes:
        return False
    return file_path.endswith((".json", ".toml", ".txt", ".bin"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Record several MocapCam devices into one FreeMoCap folder.")
    parser.add_argument("endpoint", nargs="+", help="Device endpoint as host:port")
    parser.add_argument("--session-id", default="mocapcam_multi_device")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=Path("recordings"))
    parser.add_argument("--no-depth-preview", action="store_true")
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--resolution", choices=["1280x720", "1920x1080", "3840x2160"], default="1920x1080")
    parser.add_argument("--fps", type=int, choices=[24, 30, 60, 120], default=30)
    parser.add_argument("--exposure-mode", choices=["continuous", "locked"], default="continuous")
    parser.add_argument("--exposure-bias", type=float, default=0.0)
    parser.add_argument("--focus-mode", choices=["continuous", "locked"], default="continuous")
    parser.add_argument("--white-balance-mode", choices=["continuous", "locked"], default="continuous")
    parser.add_argument("--lock-exposure", action="store_true")
    parser.add_argument("--lock-focus", action="store_true")
    parser.add_argument("--lock-white-balance", action="store_true")
    args = parser.parse_args()
    camera_settings = make_camera_settings(
        resolution=args.resolution,
        fps=args.fps,
        exposure_mode=args.exposure_mode,
        exposure_bias=args.exposure_bias,
        focus_mode=args.focus_mode,
        white_balance_mode=args.white_balance_mode,
    )

    controller = MultiDeviceCaptureController(
        endpoints=[DeviceEndpoint.parse(value) for value in args.endpoint],
        output_root=args.output,
        session_id=args.session_id,
    )
    try:
        controller.connect()
        recording_path = controller.record(
            duration_seconds=args.duration,
            depth_preview=not args.no_depth_preview,
            recover_local_files=not args.no_recovery,
            camera_settings=camera_settings,
            lock_exposure=args.lock_exposure or None,
            lock_focus=args.lock_focus or None,
            lock_white_balance=args.lock_white_balance or None,
        )
        print(f"Wrote recording to {recording_path}")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
