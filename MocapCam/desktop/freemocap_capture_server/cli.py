from __future__ import annotations

import argparse
from pathlib import Path

from .client import record_from_device
from .discovery import discover
from .multi_device import DeviceEndpoint, MultiDeviceCaptureController
from .protocol import make_camera_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive MocapCam packets and export a FreeMoCap recording folder.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Discover MocapCam devices with Bonjour/mDNS")
    discover_parser.add_argument("--timeout", type=float, default=5.0)

    record_parser = subparsers.add_parser("record", help="Record one MocapCam device")
    record_parser.add_argument("host", help="MocapCam device host or IP address")
    record_parser.add_argument("port", type=int, help="MocapCam advertised TCP port")
    record_parser.add_argument("--session-id", default="mocapcam_session", help="Recording/session folder name")
    record_parser.add_argument("--duration", type=float, default=10.0, help="Recording duration in seconds")
    record_parser.add_argument("--output", type=Path, default=Path("recordings"), help="Output root folder")
    record_parser.add_argument("--no-depth-preview", action="store_true", help="Do not request live depth preview frames")
    record_parser.add_argument("--resolution", choices=["1280x720", "1920x1080", "3840x2160"], default="1920x1080")
    record_parser.add_argument("--fps", type=int, choices=[24, 30, 60, 120], default=30)
    record_parser.add_argument("--exposure-mode", choices=["continuous", "locked"], default="continuous")
    record_parser.add_argument("--exposure-bias", type=float, default=0.0)
    record_parser.add_argument("--focus-mode", choices=["continuous", "locked"], default="continuous")
    record_parser.add_argument("--white-balance-mode", choices=["continuous", "locked"], default="continuous")
    record_parser.add_argument("--lock-exposure", action="store_true", help="Lock camera exposure before recording")
    record_parser.add_argument("--lock-focus", action="store_true", help="Lock camera focus before recording")
    record_parser.add_argument("--lock-white-balance", action="store_true", help="Lock camera white balance before recording")

    multi_parser = subparsers.add_parser("record-multi", help="Record several MocapCam devices into one session")
    multi_parser.add_argument("endpoint", nargs="+", help="Device endpoint as host:port")
    multi_parser.add_argument("--session-id", default="mocapcam_multi_device")
    multi_parser.add_argument("--duration", type=float, default=10.0)
    multi_parser.add_argument("--output", type=Path, default=Path("recordings"))
    multi_parser.add_argument("--no-depth-preview", action="store_true", help="Do not request live depth preview frames")
    multi_parser.add_argument("--no-recovery", action="store_true", help="Skip local file manifest and small-file recovery")
    multi_parser.add_argument("--resolution", choices=["1280x720", "1920x1080", "3840x2160"], default="1920x1080")
    multi_parser.add_argument("--fps", type=int, choices=[24, 30, 60, 120], default=30)
    multi_parser.add_argument("--exposure-mode", choices=["continuous", "locked"], default="continuous")
    multi_parser.add_argument("--exposure-bias", type=float, default=0.0)
    multi_parser.add_argument("--focus-mode", choices=["continuous", "locked"], default="continuous")
    multi_parser.add_argument("--white-balance-mode", choices=["continuous", "locked"], default="continuous")
    multi_parser.add_argument("--lock-exposure", action="store_true", help="Lock camera exposure before recording")
    multi_parser.add_argument("--lock-focus", action="store_true", help="Lock camera focus before recording")
    multi_parser.add_argument("--lock-white-balance", action="store_true", help="Lock camera white balance before recording")
    args = parser.parse_args()

    if args.command == "discover":
        for device in discover(timeout_seconds=args.timeout):
            print(f"{device.name}\t{device.host}\t{device.port}")
        return

    if args.command == "record-multi":
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
        finally:
            controller.close()
        print(f"Wrote recording to {recording_path}")
        return

    camera_settings = make_camera_settings(
        resolution=args.resolution,
        fps=args.fps,
        exposure_mode=args.exposure_mode,
        exposure_bias=args.exposure_bias,
        focus_mode=args.focus_mode,
        white_balance_mode=args.white_balance_mode,
    )
    recording_path = record_from_device(
        host=args.host,
        port=args.port,
        output_root=args.output,
        session_id=args.session_id,
        duration_seconds=args.duration,
        depth_preview=not args.no_depth_preview,
        camera_settings=camera_settings,
        lock_exposure=args.lock_exposure or None,
        lock_focus=args.lock_focus or None,
        lock_white_balance=args.lock_white_balance or None,
    )
    print(f"Wrote recording to {recording_path}")


if __name__ == "__main__":
    main()
