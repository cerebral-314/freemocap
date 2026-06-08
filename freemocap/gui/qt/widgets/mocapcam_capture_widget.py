import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MocapCamEndpoint:
    name: str
    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def _find_mocapcam_desktop_path() -> Optional[Path]:
    repo_root = Path(__file__).resolve().parents[4]
    candidates = [
        repo_root / "MocapCam" / "desktop",
        Path.cwd() / "MocapCam" / "desktop",
    ]
    for candidate in candidates:
        if (candidate / "freemocap_capture_server").exists():
            return candidate
    return None


def _ensure_mocapcam_capture_server_importable() -> Path:
    desktop_path = _find_mocapcam_desktop_path()
    if desktop_path is None:
        raise RuntimeError("Could not find MocapCam/desktop/freemocap_capture_server in this checkout.")

    desktop_path_string = str(desktop_path)
    if desktop_path_string not in sys.path:
        sys.path.insert(0, desktop_path_string)
    return desktop_path


class MocapCamDiscoveryThreadWorker(QThread):
    discovered = Signal(list)
    failed = Signal(str)
    in_progress = Signal(str)

    def __init__(self, timeout_seconds: float, parent=None):
        super().__init__(parent=parent)
        self._timeout_seconds = timeout_seconds

    def run(self) -> None:
        try:
            _ensure_mocapcam_capture_server_importable()
            from freemocap_capture_server.discovery import discover

            self.in_progress.emit(f"Discovering MocapCam devices for {self._timeout_seconds:.1f}s...")
            devices = discover(timeout_seconds=self._timeout_seconds)
            self.discovered.emit(
                [
                    {"name": device.name, "host": device.host, "port": device.port}
                    for device in devices
                ]
            )
        except Exception as exc:
            logger.exception("MocapCam discovery failed")
            self.failed.emit(str(exc))


class MocapCamCaptureThreadWorker(QThread):
    recording_finished = Signal(str)
    failed = Signal(str)
    in_progress = Signal(str)

    def __init__(
        self,
        endpoints: list[MocapCamEndpoint],
        output_root: Path,
        session_id: str,
        duration_seconds: float,
        recover_local_files: bool,
        camera_settings: dict,
        parent=None,
    ):
        super().__init__(parent=parent)
        self._endpoints = endpoints
        self._output_root = output_root
        self._session_id = session_id
        self._duration_seconds = duration_seconds
        self._recover_local_files = recover_local_files
        self._camera_settings = camera_settings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.in_progress.emit("Stop requested. Sending stop commands when the receiver loop wakes up...")

    def run(self) -> None:
        controller = None
        try:
            _ensure_mocapcam_capture_server_importable()
            from freemocap_capture_server.multi_device import DeviceEndpoint, MultiDeviceCaptureController

            self.in_progress.emit(
                f"Connecting to {len(self._endpoints)} MocapCam device(s) for session {self._session_id}..."
            )
            controller = MultiDeviceCaptureController(
                endpoints=[
                    DeviceEndpoint(host=endpoint.host, port=endpoint.port)
                    for endpoint in self._endpoints
                ],
                output_root=self._output_root,
                session_id=self._session_id,
            )
            controller.connect()
            recording_path = controller.record(
                duration_seconds=self._duration_seconds,
                recover_local_files=self._recover_local_files,
                camera_settings=self._camera_settings,
                cancel_event=self._cancel_event,
                status_callback=self.in_progress.emit,
            )
            self.recording_finished.emit(str(recording_path))
        except Exception as exc:
            logger.exception("MocapCam capture failed")
            self.failed.emit(str(exc))
        finally:
            if controller is not None:
                controller.close()


class MocapCamCaptureWidget(QWidget):
    recording_finished_signal = Signal(str)

    def __init__(self, recording_root_path: Path, parent=None):
        super().__init__(parent=parent)
        self._recording_root_path = Path(recording_root_path)
        self._discovery_worker: MocapCamDiscoveryThreadWorker | None = None
        self._capture_worker: MocapCamCaptureThreadWorker | None = None

        self._layout = QVBoxLayout()
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(self._layout)

        self._create_discovery_group()
        self._create_capture_settings_group()
        self._create_capture_log()
        self._refresh_backend_status()

    def _create_discovery_group(self) -> None:
        group_box = QGroupBox("MocapCam Devices")
        layout = QVBoxLayout()
        group_box.setLayout(layout)

        controls_layout = QHBoxLayout()
        self._discovery_timeout_spinbox = QDoubleSpinBox()
        self._discovery_timeout_spinbox.setRange(0.5, 30.0)
        self._discovery_timeout_spinbox.setSingleStep(0.5)
        self._discovery_timeout_spinbox.setValue(3.0)
        self._discover_button = QPushButton("Discover")
        self._discover_button.clicked.connect(self._start_discovery)
        controls_layout.addWidget(QLabel("Discovery timeout"))
        controls_layout.addWidget(self._discovery_timeout_spinbox)
        controls_layout.addWidget(self._discover_button)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        manual_layout = QHBoxLayout()
        self._manual_endpoint_line_edit = QLineEdit()
        self._manual_endpoint_line_edit.setPlaceholderText("192.168.1.50:54321")
        self._add_endpoint_button = QPushButton("Add Endpoint")
        self._add_endpoint_button.clicked.connect(self._add_manual_endpoint)
        self._remove_endpoint_button = QPushButton("Remove Selected")
        self._remove_endpoint_button.clicked.connect(self._remove_selected_endpoints)
        self._clear_endpoints_button = QPushButton("Clear")
        self._clear_endpoints_button.clicked.connect(self._clear_endpoints)
        manual_layout.addWidget(self._manual_endpoint_line_edit, 1)
        manual_layout.addWidget(self._add_endpoint_button)
        manual_layout.addWidget(self._remove_endpoint_button)
        manual_layout.addWidget(self._clear_endpoints_button)
        layout.addLayout(manual_layout)

        self._endpoint_table = QTableWidget(0, 4)
        self._endpoint_table.setHorizontalHeaderLabels(["Device", "Host", "Port", "Status"])
        self._endpoint_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._endpoint_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._endpoint_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._endpoint_table.setMinimumHeight(180)
        layout.addWidget(self._endpoint_table)

        self._layout.addWidget(group_box)

    def _create_capture_settings_group(self) -> None:
        group_box = QGroupBox("Capture")
        layout = QVBoxLayout()
        group_box.setLayout(layout)

        form_layout = QFormLayout()
        self._session_id_line_edit = QLineEdit(self._default_session_id())
        self._duration_spinbox = QDoubleSpinBox()
        self._duration_spinbox.setRange(1.0, 60.0 * 60.0)
        self._duration_spinbox.setDecimals(1)
        self._duration_spinbox.setSingleStep(5.0)
        self._duration_spinbox.setValue(20.0)

        output_layout = QHBoxLayout()
        self._output_root_line_edit = QLineEdit(str(self._recording_root_path))
        self._browse_output_button = QPushButton("Browse")
        self._browse_output_button.clicked.connect(self._browse_output_root)
        output_layout.addWidget(self._output_root_line_edit, 1)
        output_layout.addWidget(self._browse_output_button)

        form_layout.addRow("Session ID", self._session_id_line_edit)
        form_layout.addRow("Duration (s)", self._duration_spinbox)
        form_layout.addRow("Recording root", output_layout)
        layout.addLayout(form_layout)

        settings_layout = QFormLayout()
        self._resolution_combobox = QComboBox()
        self._resolution_combobox.addItems(["1280x720", "1920x1080", "3840x2160"])
        self._resolution_combobox.setCurrentText("1920x1080")

        self._fps_combobox = QComboBox()
        self._fps_combobox.addItems(["24", "30", "60", "120"])
        self._fps_combobox.setCurrentText("30")

        self._exposure_mode_combobox = QComboBox()
        self._exposure_mode_combobox.addItems(["continuous", "locked"])
        self._exposure_bias_spinbox = QDoubleSpinBox()
        self._exposure_bias_spinbox.setRange(-4.0, 4.0)
        self._exposure_bias_spinbox.setDecimals(1)
        self._exposure_bias_spinbox.setSingleStep(0.1)

        self._focus_mode_combobox = QComboBox()
        self._focus_mode_combobox.addItems(["continuous", "locked"])
        self._white_balance_mode_combobox = QComboBox()
        self._white_balance_mode_combobox.addItems(["continuous", "locked"])

        settings_layout.addRow("Resolution", self._resolution_combobox)
        settings_layout.addRow("FPS", self._fps_combobox)
        settings_layout.addRow("Exposure", self._exposure_mode_combobox)
        settings_layout.addRow("Exposure bias", self._exposure_bias_spinbox)
        settings_layout.addRow("Focus", self._focus_mode_combobox)
        settings_layout.addRow("White balance", self._white_balance_mode_combobox)
        layout.addLayout(settings_layout)

        options_layout = QHBoxLayout()
        self._recover_local_files_checkbox = QCheckBox("Recover local files")
        self._recover_local_files_checkbox.setChecked(True)
        options_layout.addWidget(self._recover_local_files_checkbox)
        options_layout.addStretch()
        layout.addLayout(options_layout)

        action_layout = QHBoxLayout()
        self._start_capture_button = QPushButton("Record MocapCam Session")
        self._start_capture_button.clicked.connect(self._start_capture)
        self._stop_capture_button = QPushButton("Stop")
        self._stop_capture_button.setEnabled(False)
        self._stop_capture_button.clicked.connect(self._stop_capture)
        self._new_session_button = QPushButton("New Session Name")
        self._new_session_button.clicked.connect(self._set_new_session_name)
        action_layout.addWidget(self._start_capture_button)
        action_layout.addWidget(self._stop_capture_button)
        action_layout.addWidget(self._new_session_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        self._backend_status_label = QLabel()
        self._backend_status_label.setWordWrap(True)
        layout.addWidget(self._backend_status_label)

        self._layout.addWidget(group_box)

    def _create_capture_log(self) -> None:
        self._capture_log = QPlainTextEdit()
        self._capture_log.setReadOnly(True)
        self._capture_log.setMinimumHeight(180)
        self._layout.addWidget(self._capture_log)

    def _refresh_backend_status(self) -> None:
        desktop_path = _find_mocapcam_desktop_path()
        if desktop_path is None:
            self._backend_status_label.setText("MocapCam backend not found. Expected MocapCam/desktop in this checkout.")
            self._start_capture_button.setEnabled(False)
            self._discover_button.setEnabled(False)
            return

        self._backend_status_label.setText(f"MocapCam backend: {desktop_path}")
        self._start_capture_button.setEnabled(True)
        self._discover_button.setEnabled(True)

    def _start_discovery(self) -> None:
        self._discover_button.setEnabled(False)
        self._discovery_worker = MocapCamDiscoveryThreadWorker(
            timeout_seconds=float(self._discovery_timeout_spinbox.value()),
            parent=self,
        )
        self._discovery_worker.in_progress.connect(self._append_log)
        self._discovery_worker.discovered.connect(self._handle_discovered_devices)
        self._discovery_worker.failed.connect(self._handle_discovery_failed)
        self._discovery_worker.finished.connect(lambda: self._discover_button.setEnabled(True))
        self._discovery_worker.start()

    def _handle_discovered_devices(self, devices: list[dict]) -> None:
        if not devices:
            self._append_log("No MocapCam devices discovered. Add host:port endpoints manually if Bonjour is unavailable.")
            return

        for device in devices:
            self._add_endpoint(
                MocapCamEndpoint(
                    name=str(device["name"]),
                    host=str(device["host"]),
                    port=int(device["port"]),
                ),
                status="discovered",
            )
        self._append_log(f"Discovered {len(devices)} MocapCam device(s).")

    def _handle_discovery_failed(self, message: str) -> None:
        self._append_log(f"Discovery failed: {message}")

    def _add_manual_endpoint(self) -> None:
        value = self._manual_endpoint_line_edit.text().strip()
        if not value:
            return
        try:
            host, port_string = value.rsplit(":", 1)
            endpoint = MocapCamEndpoint(name=value, host=host.strip(), port=int(port_string))
        except ValueError:
            self._append_log("Manual endpoint must be host:port.")
            return

        self._add_endpoint(endpoint, status="manual")
        self._manual_endpoint_line_edit.clear()

    def _add_endpoint(self, endpoint: MocapCamEndpoint, status: str) -> None:
        for row_index in range(self._endpoint_table.rowCount()):
            host = self._endpoint_table.item(row_index, 1).text()
            port = int(self._endpoint_table.item(row_index, 2).text())
            if host == endpoint.host and port == endpoint.port:
                self._endpoint_table.item(row_index, 0).setText(endpoint.name)
                self._endpoint_table.item(row_index, 3).setText(status)
                return

        row_index = self._endpoint_table.rowCount()
        self._endpoint_table.insertRow(row_index)
        for column_index, value in enumerate([endpoint.name, endpoint.host, str(endpoint.port), status]):
            self._endpoint_table.setItem(row_index, column_index, QTableWidgetItem(value))

    def _remove_selected_endpoints(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self._endpoint_table.selectedIndexes()},
            reverse=True,
        )
        for row_index in selected_rows:
            self._endpoint_table.removeRow(row_index)

    def _clear_endpoints(self) -> None:
        self._endpoint_table.setRowCount(0)

    def _selected_endpoints(self) -> list[MocapCamEndpoint]:
        endpoints: list[MocapCamEndpoint] = []
        for row_index in range(self._endpoint_table.rowCount()):
            endpoints.append(
                MocapCamEndpoint(
                    name=self._endpoint_table.item(row_index, 0).text(),
                    host=self._endpoint_table.item(row_index, 1).text(),
                    port=int(self._endpoint_table.item(row_index, 2).text()),
                )
            )
        return endpoints

    def _start_capture(self) -> None:
        endpoints = self._selected_endpoints()
        if not endpoints:
            self._append_log("Add at least one MocapCam endpoint before recording.")
            return

        session_id = self._session_id_line_edit.text().strip()
        if not session_id:
            self._append_log("Session ID cannot be empty.")
            return

        output_root = Path(self._output_root_line_edit.text()).expanduser()
        self._capture_worker = MocapCamCaptureThreadWorker(
            endpoints=endpoints,
            output_root=output_root,
            session_id=session_id,
            duration_seconds=float(self._duration_spinbox.value()),
            recover_local_files=self._recover_local_files_checkbox.isChecked(),
            camera_settings=self._camera_settings(),
            parent=self,
        )
        self._capture_worker.in_progress.connect(self._append_log)
        self._capture_worker.recording_finished.connect(self._handle_capture_finished)
        self._capture_worker.failed.connect(self._handle_capture_failed)
        self._capture_worker.finished.connect(lambda: self._set_capture_running(False))

        self._set_capture_running(True)
        self._append_log(f"Starting MocapCam capture: {session_id}")
        self._capture_worker.start()

    def _stop_capture(self) -> None:
        if self._capture_worker is not None:
            self._capture_worker.cancel()

    def _handle_capture_finished(self, recording_path: str) -> None:
        self._append_log(f"MocapCam recording written to {recording_path}")
        self.recording_finished_signal.emit(recording_path)

    def _handle_capture_failed(self, message: str) -> None:
        self._append_log(f"Capture failed: {message}")

    def _set_capture_running(self, is_running: bool) -> None:
        self._start_capture_button.setEnabled(not is_running)
        self._stop_capture_button.setEnabled(is_running)
        self._discover_button.setEnabled(not is_running)
        self._add_endpoint_button.setEnabled(not is_running)
        self._remove_endpoint_button.setEnabled(not is_running)
        self._clear_endpoints_button.setEnabled(not is_running)
        self._browse_output_button.setEnabled(not is_running)
        self._resolution_combobox.setEnabled(not is_running)
        self._fps_combobox.setEnabled(not is_running)
        self._exposure_mode_combobox.setEnabled(not is_running)
        self._exposure_bias_spinbox.setEnabled(not is_running)
        self._focus_mode_combobox.setEnabled(not is_running)
        self._white_balance_mode_combobox.setEnabled(not is_running)

    def _browse_output_root(self) -> None:
        selected_folder = QFileDialog.getExistingDirectory(
            self,
            "Select MocapCam recording root",
            self._output_root_line_edit.text(),
        )
        if selected_folder:
            self._output_root_line_edit.setText(selected_folder)

    def _set_new_session_name(self) -> None:
        self._session_id_line_edit.setText(self._default_session_id())

    def _append_log(self, message: str) -> None:
        logger.info(message)
        self._capture_log.appendPlainText(message)

    @staticmethod
    def _default_session_id() -> str:
        return f"mocapcam_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _camera_settings(self) -> dict:
        _ensure_mocapcam_capture_server_importable()
        from freemocap_capture_server.protocol import make_camera_settings

        return make_camera_settings(
            resolution=self._resolution_combobox.currentText(),
            fps=int(self._fps_combobox.currentText()),
            exposure_mode=self._exposure_mode_combobox.currentText(),
            exposure_bias=float(self._exposure_bias_spinbox.value()),
            focus_mode=self._focus_mode_combobox.currentText(),
            white_balance_mode=self._white_balance_mode_combobox.currentText(),
        )
