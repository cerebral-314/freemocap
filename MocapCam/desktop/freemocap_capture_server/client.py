from __future__ import annotations

import selectors
import socket
import threading
import time
import uuid
from pathlib import Path

from .protocol import CapturePacket, extract_packets, make_command, merge_legacy_camera_locks
from .recorder import CaptureSessionRecorder


def monotonic_ns() -> int:
    return time.monotonic_ns()


class CaptureClient:
    def __init__(
        self,
        host: str,
        port: int,
        recorder: CaptureSessionRecorder,
        socket_timeout_seconds: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.recorder = recorder
        self.socket_timeout_seconds = socket_timeout_seconds
        self.buffer = bytearray()
        self.socket: socket.socket | None = None
        self.seen_device_ids: set[str] = set()

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.socket_timeout_seconds)
        sock.setblocking(False)
        self.socket = sock

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def send_command(self, command: str, **values: object) -> None:
        if self.socket is None:
            raise RuntimeError("CaptureClient is not connected")
        self.socket.sendall(make_command(command, **values))

    def send_ping(self) -> None:
        self.send_command(
            "ping",
            request_id=str(uuid.uuid4()),
            server_time_send_ns=monotonic_ns(),
        )

    def service_once(self, timeout_seconds: float = 0.05) -> list[CapturePacket]:
        if self.socket is None:
            raise RuntimeError("CaptureClient is not connected")
        selector = selectors.DefaultSelector()
        selector.register(self.socket, selectors.EVENT_READ)
        packets: list[CapturePacket] = []
        for key, _ in selector.select(timeout=timeout_seconds):
            packets.extend(self._receive(key.fileobj))
        selector.close()
        return packets

    def request_local_file_manifest(self) -> None:
        self.send_command("list_local_files", request_id=str(uuid.uuid4()))

    def request_local_file(self, file_path: str, file_size_bytes: int, chunk_size: int = 262_144) -> None:
        for offset in range(0, file_size_bytes, chunk_size):
            self.send_command(
                "download_local_file",
                request_id=str(uuid.uuid4()),
                file_path=file_path,
                offset=offset,
                length=chunk_size,
            )

    def run_recording(
        self,
        duration_seconds: float,
        depth_preview: bool = True,
        sync_warmup_seconds: float = 2.0,
        ping_interval_seconds: float = 0.25,
        camera_settings: dict[str, object] | None = None,
        lock_exposure: bool | None = None,
        lock_focus: bool | None = None,
        lock_white_balance: bool | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if self.socket is None:
            self.connect()
        assert self.socket is not None

        selector = selectors.DefaultSelector()
        selector.register(self.socket, selectors.EVENT_READ)

        merged_camera_settings = merge_legacy_camera_locks(
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
        )
        if merged_camera_settings is not None:
            self.send_command(
                "set_camera_settings",
                camera_settings=merged_camera_settings,
            )
        self.send_command("start_preview")

        next_ping_at = time.monotonic()
        warmup_deadline = time.monotonic() + sync_warmup_seconds
        while time.monotonic() < warmup_deadline and not is_cancelled(cancel_event):
            next_ping_at = self._service_socket(selector, next_ping_at, ping_interval_seconds)

        if is_cancelled(cancel_event):
            self.send_command("stop_preview")
            self.recorder.finalize()
            return

        server_start_time_ns = monotonic_ns() + 1_000_000_000
        for device_id in list(self.recorder.devices):
            self.send_command(
                "arm_recording",
                session_id=self.recorder.session_id,
                start_at_server_time_ns=server_start_time_ns,
                start_at_device_time_ns=self.recorder.estimated_device_start_time_ns(device_id, server_start_time_ns),
                camera_settings=merged_camera_settings,
                lock_exposure=lock_exposure,
                lock_focus=lock_focus,
                lock_white_balance=lock_white_balance,
            )
        if not self.recorder.devices:
            self.send_command(
                "start_recording",
                session_id=self.recorder.session_id,
                camera_settings=merged_camera_settings,
                lock_exposure=lock_exposure,
                lock_focus=lock_focus,
                lock_white_balance=lock_white_balance,
            )

        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline and not is_cancelled(cancel_event):
            next_ping_at = self._service_socket(selector, next_ping_at, ping_interval_seconds)

        self.send_command("stop_recording")
        self._drain(selector, seconds=1.0)
        self.recorder.finalize()

    def _service_socket(
        self,
        selector: selectors.BaseSelector,
        next_ping_at: float,
        ping_interval_seconds: float,
    ) -> float:
        now = time.monotonic()
        if now >= next_ping_at:
            self.send_ping()
            next_ping_at = now + ping_interval_seconds

        for key, _ in selector.select(timeout=0.05):
            self._receive(key.fileobj)
        return next_ping_at

    def _receive(self, sock: socket.socket) -> list[CapturePacket]:
        chunk = sock.recv(1024 * 1024)
        if not chunk:
            raise ConnectionError("MocapCam connection closed")
        self.buffer.extend(chunk)
        packets = extract_packets(self.buffer)
        server_receive_time_ns = monotonic_ns()
        for packet in packets:
            device_id = packet_device_id(packet)
            if device_id:
                self.seen_device_ids.add(device_id)
            self.recorder.handle_packet(packet, server_receive_time_ns=server_receive_time_ns)
        return packets

    def _drain(self, selector: selectors.BaseSelector, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for key, _ in selector.select(timeout=0.05):
                self._receive(key.fileobj)


def record_from_device(
    host: str,
    port: int,
    output_root: Path,
    session_id: str,
    duration_seconds: float,
    depth_preview: bool = True,
    camera_settings: dict[str, object] | None = None,
    lock_exposure: bool | None = None,
    lock_focus: bool | None = None,
    lock_white_balance: bool | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    recorder = CaptureSessionRecorder(output_root=output_root, session_id=session_id)
    client = CaptureClient(host=host, port=port, recorder=recorder)
    try:
        client.connect()
        client.run_recording(
            duration_seconds=duration_seconds,
            depth_preview=depth_preview,
            camera_settings=camera_settings,
            lock_exposure=lock_exposure,
            lock_focus=lock_focus,
            lock_white_balance=lock_white_balance,
            cancel_event=cancel_event,
        )
    finally:
        client.close()
    return recorder.recording_path


def packet_device_id(packet: CapturePacket) -> str | None:
    if "status" in packet.metadata:
        return packet.metadata["status"].get("device_id")
    for key in ("metadata", "event", "sync", "manifest", "chunk"):
        if key in packet.metadata:
            return packet.metadata[key].get("device_id")
    return None


def is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()
