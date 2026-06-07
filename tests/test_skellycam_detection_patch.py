import importlib.util
import sys
import types
from pathlib import Path

_PATCH_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "freemocap" / "gui" / "qt" / "utilities" / "skellycam_detection_patch.py"
)
_PATCH_MODULE_SPEC = importlib.util.spec_from_file_location("skellycam_detection_patch", _PATCH_MODULE_PATH)
skellycam_detection_patch = importlib.util.module_from_spec(_PATCH_MODULE_SPEC)
_PATCH_MODULE_SPEC.loader.exec_module(skellycam_detection_patch)


class FakeFoundCameraCache:
    def __init__(self, number_of_cameras_found, cameras_found_list):
        self.number_of_cameras_found = number_of_cameras_found
        self.cameras_found_list = cameras_found_list


class FakeImage:
    shape = (480, 640, 3)
    size = 480 * 640 * 3

    def __init__(self, mean_value=1):
        self.mean_value = mean_value

    def __array__(self, dtype=None):
        import numpy as np

        return np.full(self.shape, self.mean_value, dtype=dtype or np.uint8)


class FakeCapture:
    def __init__(self, cam_id, frames=None):
        self.cam_id = cam_id
        self.frames = frames or [(False, None)]
        self.read_calls = 0
        self.release_calls = 0

    def read(self):
        self.read_calls += 1
        frame_index = min(self.read_calls - 1, len(self.frames) - 1)
        return self.frames[frame_index]

    def release(self):
        self.release_calls += 1


def test_skellycam_detection_patch_accepts_camera_after_valid_frames(monkeypatch):
    fake_cv2, captures = _install_fake_skellycam_modules(monkeypatch)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    result = DetectPossibleCameras().find_available_cameras()

    assert result.cameras_found_list == ["0"]
    assert result.number_of_cameras_found == 1
    assert captures[0].read_calls == 2
    assert captures[0].release_calls == 1
    assert captures[1].release_calls == 1
    assert fake_cv2.backends_seen == ["FAKE_BACKEND", "FAKE_BACKEND"]


def test_skellycam_detection_patch_is_idempotent(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    first_patched_detector = DetectPossibleCameras.find_available_cameras
    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    assert DetectPossibleCameras.find_available_cameras is first_patched_detector


def test_skellycam_detection_patch_skips_static_non_black_virtual_camera(monkeypatch):
    _install_fake_skellycam_modules(
        monkeypatch,
        captures={
            0: FakeCapture(cam_id=0, frames=[(True, FakeImage(mean_value=80)), (True, FakeImage(mean_value=80))]),
            1: FakeCapture(cam_id=1, frames=[(False, None)]),
        },
    )

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    result = DetectPossibleCameras().find_available_cameras()

    assert result.cameras_found_list == []
    assert result.number_of_cameras_found == 0


def test_skellycam_detection_patch_prefers_msmf_for_droidcam_devices(monkeypatch):
    fake_cv2, captures = _install_fake_skellycam_modules(
        monkeypatch,
        captures={
            0: FakeCapture(cam_id=0, frames=[(True, FakeImage(mean_value=80)), (True, FakeImage(mean_value=80))]),
            1: FakeCapture(cam_id=1, frames=[(False, None)]),
        },
    )
    skellycam_detection_patch._DIRECTSHOW_CAMERA_NAMES_CACHE = ["DroidCam Video"]

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    result = DetectPossibleCameras().find_available_cameras()

    assert result.cameras_found_list == ["0"]
    assert fake_cv2.backends_seen[0] == "FAKE_MSMF"
    assert skellycam_detection_patch._CAMERA_BACKEND_OVERRIDES["0"] == skellycam_detection_patch._DROIDCAM_BACKEND_NAME
    assert "0" in skellycam_detection_patch._CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG
    assert captures[0].release_calls == 1


def test_skellycam_detection_patch_uses_ffmpeg_for_obs_directshow_devices(monkeypatch):
    _fake_cv2, captures = _install_fake_skellycam_modules(
        monkeypatch,
        captures={
            0: FakeCapture(cam_id=0, frames=[(True, FakeImage(mean_value=80)), (True, FakeImage(mean_value=90))]),
            1: FakeCapture(cam_id=1, frames=[(False, None)]),
        },
    )
    skellycam_detection_patch._DIRECTSHOW_CAMERA_NAMES_CACHE = ["OBS-Camera"]
    monkeypatch.setattr(skellycam_detection_patch, "_can_use_ffmpeg_directshow_capture", lambda: True)
    monkeypatch.setattr(
        skellycam_detection_patch,
        "_FfmpegDirectShowCameraCapture",
        lambda camera_id: captures[camera_id],
    )

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    result = DetectPossibleCameras().find_available_cameras()

    assert result.cameras_found_list == ["0"]
    assert "0" in skellycam_detection_patch._CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE
    assert "0" in skellycam_detection_patch._CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG
    assert captures[0].read_calls == 2


def test_skellycam_detection_patch_skips_obs_opencv_fallback_without_ffmpeg_capture(monkeypatch):
    _fake_cv2, captures = _install_fake_skellycam_modules(
        monkeypatch,
        captures={
            0: FakeCapture(cam_id=0, frames=[(True, FakeImage(mean_value=80)), (True, FakeImage(mean_value=90))]),
            1: FakeCapture(cam_id=1, frames=[(False, None)]),
        },
    )
    skellycam_detection_patch._DIRECTSHOW_CAMERA_NAMES_CACHE = ["OBS-Camera"]
    monkeypatch.setattr(skellycam_detection_patch, "_can_use_ffmpeg_directshow_capture", lambda: False)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    result = DetectPossibleCameras().find_available_cameras()

    assert result.cameras_found_list == []
    assert captures[0].read_calls == 0


def test_skellycam_detection_patch_skips_configuration_for_slow_cameras(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch, perf_counter_values=[0.0, 0.6, 0.6, 1.3])

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras
    from skellycam.opencv.config import apply_config
    from skellycam.opencv.camera import internal_camera_thread

    DetectPossibleCameras().find_available_cameras()

    config = types.SimpleNamespace(camera_id="0")
    apply_config.apply_configuration(cv2_vid_cap=object(), config=config)
    internal_camera_thread.apply_configuration(cv2_vid_cap=object(), config=config)

    assert apply_config.original_apply_configuration_calls == []


def test_skellycam_detection_patch_reads_camera_queue_without_empty_check(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.opencv.group.strategies.cam_group_queue_process import CamGroupQueueProcess

    class FakeQueue:
        def empty(self):
            raise AssertionError("Queue.empty() should not be used to decide whether frames are available")

        def get(self, block=True):
            assert block is False
            return "frame-payload"

    cam_group_process = CamGroupQueueProcess()
    cam_group_process._queues = {"0": FakeQueue()}

    assert cam_group_process.get_current_frame_by_camera_id("0") == "frame-payload"


def test_skellycam_detection_patch_uses_frame_count_for_camera_readiness(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.opencv.camera.camera import Camera

    first_frame = types.SimpleNamespace(success=True, image=FakeImage(), number_of_frames_received=1)
    camera = Camera()
    camera._capture_thread = types.SimpleNamespace(_frame=first_frame, latest_frame=first_frame)

    assert camera.new_frame_ready is True
    assert camera.latest_frame is first_frame
    assert camera.new_frame_ready is False

    second_frame = types.SimpleNamespace(success=True, image=FakeImage(), number_of_frames_received=2)
    camera._capture_thread._frame = second_frame
    camera._capture_thread.latest_frame = second_frame

    assert camera.new_frame_ready is True


def test_skellycam_detection_patch_uses_same_process_strategy_for_slow_cameras(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch, perf_counter_values=[0.0, 0.6, 0.6, 1.3])

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras
    from skellycam.opencv.group.camera_group import CameraGroup

    DetectPossibleCameras().find_available_cameras()

    strategy = CameraGroup()._resolve_strategy(["0"])

    assert isinstance(strategy, skellycam_detection_patch._SameProcessCameraStrategy)


def test_skellycam_detection_patch_uses_same_process_strategy_for_cached_obs_camera_ids(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch)
    skellycam_detection_patch._DIRECTSHOW_CAMERA_NAMES_CACHE = ["DroidCam Video", "OBS-Camera"]
    monkeypatch.setattr(skellycam_detection_patch, "_can_use_ffmpeg_directshow_capture", lambda: True)

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.opencv.group.camera_group import CameraGroup

    strategy = CameraGroup()._resolve_strategy(["1"])

    assert isinstance(strategy, skellycam_detection_patch._SameProcessCameraStrategy)
    assert "1" in skellycam_detection_patch._CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE
    assert "1" in skellycam_detection_patch._CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG


def test_same_process_strategy_filters_disabled_cameras_from_latest_frames():
    class FakeCamera:
        def __init__(self, use_this_camera, latest_frame):
            self._config = types.SimpleNamespace(use_this_camera=use_this_camera)
            self._latest_frame = latest_frame

        @property
        def is_capturing_frames(self):
            return self._config.use_this_camera

        @property
        def new_frame_ready(self):
            return True

        @property
        def latest_frame(self):
            return self._latest_frame

    strategy = skellycam_detection_patch._SameProcessCameraStrategy(["0", "1"])
    strategy._cameras = {
        "0": FakeCamera(use_this_camera=False, latest_frame="disabled-frame"),
        "1": FakeCamera(use_this_camera=True, latest_frame="active-frame"),
    }

    assert strategy.get_latest_frames() == {"1": "active-frame"}
    assert strategy.queue_size == {"0": 0, "1": 1}


def test_save_synchronized_videos_preserves_physical_camera_ids(monkeypatch, tmp_path):
    saved_video_paths = []

    class FakeVideoRecorder:
        def save_frame_list_to_video_file(self, frame_payload_list, video_file_save_path):
            saved_video_paths.append(Path(video_file_save_path))

    class FakeRecorder:
        def __init__(self, frame_payload_list):
            self.frame_payload_list = frame_payload_list

        @property
        def number_of_frames(self):
            return len(self.frame_payload_list)

    _install_fake_video_save_modules(monkeypatch, FakeVideoRecorder)

    frames = [
        types.SimpleNamespace(timestamp_ns=100, image=FakeImage()),
        types.SimpleNamespace(timestamp_ns=200, image=FakeImage()),
    ]

    skellycam_detection_patch._save_synchronized_videos_preserving_camera_ids(
        dictionary_of_video_recorders={"1": FakeRecorder(frames), "3": FakeRecorder(frames)},
        folder_to_save_videos=tmp_path,
        create_diagnostic_plots_bool=False,
    )

    assert [path.name for path in saved_video_paths] == [
        "Camera_001_synchronized.mp4",
        "Camera_003_synchronized.mp4",
    ]


def test_parameter_tree_keeps_enabled_camera_settings_editable(monkeypatch):
    qt_label_strings_module = types.ModuleType("skellycam.gui.qt.utilities.qt_label_strings")
    qt_label_strings_module.USE_THIS_CAMERA_STRING = "Use this camera?"
    monkeypatch.setitem(sys.modules, "skellycam.gui.qt.utilities.qt_label_strings", qt_label_strings_module)

    class FakeParameter:
        def __init__(self, name, value=None):
            self._name = name
            self._value = value
            self.enabled = None
            self.readonly = None

        def name(self):
            return self._name

        def value(self):
            return self._value

        def setOpts(self, enabled):
            self.enabled = enabled

        def setReadonly(self, readonly):
            self.readonly = readonly

    class FakeParameterGroup:
        def __init__(self, use_this_camera):
            self.use_this_camera = FakeParameter("Use this camera?", use_this_camera)
            self.width = FakeParameter("Resolution Width")
            self.height = FakeParameter("Resolution Height")

        def param(self, name):
            assert name == "Use this camera?"
            return self.use_this_camera

        def children(self):
            return [self.use_this_camera, self.width, self.height]

    enabled_group = FakeParameterGroup(use_this_camera=True)
    skellycam_detection_patch._enable_or_disable_camera_settings_with_editable_active_cameras(
        self=object(), camera_config_parameter_group=enabled_group
    )

    assert enabled_group.width.enabled is True
    assert enabled_group.width.readonly is False
    assert enabled_group.height.enabled is True
    assert enabled_group.height.readonly is False

    disabled_group = FakeParameterGroup(use_this_camera=False)
    skellycam_detection_patch._enable_or_disable_camera_settings_with_editable_active_cameras(
        self=object(), camera_config_parameter_group=disabled_group
    )

    assert disabled_group.width.enabled is False
    assert disabled_group.width.readonly is True
    assert disabled_group.height.enabled is False
    assert disabled_group.height.readonly is True


def test_skellycam_detection_patch_uses_read_for_slow_camera_frames(monkeypatch):
    _install_fake_skellycam_modules(monkeypatch, perf_counter_values=[0.0, 0.6, 0.6, 1.3])

    skellycam_detection_patch.allow_slow_or_static_cameras_in_skellycam_detection()

    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras
    from skellycam.opencv.camera.internal_camera_thread import VideoCaptureThread

    DetectPossibleCameras().find_available_cameras()

    capture = FakeCapture(cam_id=0, frames=[(True, FakeImage(mean_value=3))])
    camera_thread = VideoCaptureThread()
    camera_thread._config = types.SimpleNamespace(camera_id="0", rotate_video_cv2_code=-1)
    camera_thread._cv2_video_capture = capture
    camera_thread._new_frame_ready = False
    camera_thread._number_of_frames_received = 0

    frame = camera_thread._get_next_frame()

    assert capture.read_calls == 1
    assert frame.success is True
    assert frame.number_of_frames_received == 1
    assert frame.camera_id == "0"


def _install_fake_video_save_modules(monkeypatch, FakeVideoRecorder):
    diagnostics_module = types.ModuleType("skellycam.diagnostics")
    create_diagnostic_plots_module = types.ModuleType("skellycam.diagnostics.create_diagnostic_plots")
    video_recorder_package_module = types.ModuleType("skellycam.opencv.video_recorder")
    save_synchronized_videos_module = types.ModuleType("skellycam.opencv.video_recorder.save_synchronized_videos")
    video_recorder_module = types.ModuleType("skellycam.opencv.video_recorder.video_recorder")
    tests_module = types.ModuleType("skellycam.tests")
    test_frame_timestamp_synchronization_module = types.ModuleType(
        "skellycam.tests.test_frame_timestamp_synchronization"
    )
    test_synchronized_video_frame_counts_module = types.ModuleType(
        "skellycam.tests.test_synchronized_video_frame_counts"
    )

    create_diagnostic_plots_module.create_diagnostic_plots = lambda **_kwargs: None
    save_synchronized_videos_module.get_nearest_frame = lambda frame_list, reference_frame: frame_list[0]
    video_recorder_module.VideoRecorder = FakeVideoRecorder
    test_frame_timestamp_synchronization_module.test_frame_timestamp_synchronization = lambda **_kwargs: None
    test_synchronized_video_frame_counts_module.test_synchronized_video_frame_counts = lambda **_kwargs: None

    monkeypatch.setitem(sys.modules, "skellycam.diagnostics", diagnostics_module)
    monkeypatch.setitem(
        sys.modules,
        "skellycam.diagnostics.create_diagnostic_plots",
        create_diagnostic_plots_module,
    )
    monkeypatch.setitem(sys.modules, "skellycam.opencv.video_recorder", video_recorder_package_module)
    monkeypatch.setitem(
        sys.modules,
        "skellycam.opencv.video_recorder.save_synchronized_videos",
        save_synchronized_videos_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "skellycam.opencv.video_recorder.video_recorder",
        video_recorder_module,
    )
    monkeypatch.setitem(sys.modules, "skellycam.tests", tests_module)
    monkeypatch.setitem(
        sys.modules,
        "skellycam.tests.test_frame_timestamp_synchronization",
        test_frame_timestamp_synchronization_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "skellycam.tests.test_synchronized_video_frame_counts",
        test_synchronized_video_frame_counts_module,
    )


def _install_fake_skellycam_modules(monkeypatch, captures=None, perf_counter_values=None):
    skellycam_detection_patch._CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG.clear()
    skellycam_detection_patch._CAMERA_BACKEND_OVERRIDES.clear()
    skellycam_detection_patch._CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE.clear()
    skellycam_detection_patch._CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE.clear()
    skellycam_detection_patch._DIRECTSHOW_CAMERA_NAMES_CACHE = []
    monkeypatch.delenv(skellycam_detection_patch._SKIP_CONFIG_CAMERA_IDS_ENV_VAR, raising=False)
    monkeypatch.delenv(skellycam_detection_patch._CAMERA_BACKEND_OVERRIDES_ENV_VAR, raising=False)
    monkeypatch.delenv(skellycam_detection_patch._DIRECTSHOW_YUV_CAMERA_IDS_ENV_VAR, raising=False)
    monkeypatch.delenv(skellycam_detection_patch._FFMPEG_DIRECTSHOW_CAMERA_IDS_ENV_VAR, raising=False)
    if captures is None:
        captures = {
            0: FakeCapture(cam_id=0, frames=[(True, FakeImage()), (True, FakeImage(mean_value=2))]),
            1: FakeCapture(cam_id=1, frames=[(False, None)]),
        }

    if perf_counter_values is not None:
        perf_counter_iterator = iter(perf_counter_values)
        monkeypatch.setattr(skellycam_detection_patch.time, "perf_counter", lambda: next(perf_counter_iterator))

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.backends_seen = []
    fake_cv2.CAP_MSMF = "FAKE_MSMF"

    def video_capture(cam_id, backend):
        fake_cv2.backends_seen.append(backend)
        return captures[cam_id]

    fake_cv2.VideoCapture = video_capture

    skellycam_module = types.ModuleType("skellycam")
    detection_module = types.ModuleType("skellycam.detection")
    detection_models_module = types.ModuleType("skellycam.detection.models")
    frame_payload_module = types.ModuleType("skellycam.detection.models.frame_payload")
    private_module = types.ModuleType("skellycam.detection.private")
    detect_possible_cameras_module = types.ModuleType("skellycam.detection.private.detect_possible_cameras")
    found_camera_cache_module = types.ModuleType("skellycam.detection.private.found_camera_cache")
    opencv_module = types.ModuleType("skellycam.opencv")
    config_module = types.ModuleType("skellycam.opencv.config")
    camera_module = types.ModuleType("skellycam.opencv.camera")
    camera_camera_module = types.ModuleType("skellycam.opencv.camera.camera")
    internal_camera_thread_module = types.ModuleType("skellycam.opencv.camera.internal_camera_thread")
    apply_config_module = types.ModuleType("skellycam.opencv.config.apply_config")
    determine_backend_module = types.ModuleType("skellycam.opencv.config.determine_backend")
    group_module = types.ModuleType("skellycam.opencv.group")
    camera_group_module = types.ModuleType("skellycam.opencv.group.camera_group")
    strategies_module = types.ModuleType("skellycam.opencv.group.strategies")
    cam_group_queue_process_module = types.ModuleType("skellycam.opencv.group.strategies.cam_group_queue_process")

    class DetectPossibleCameras:
        def find_available_cameras(self):
            return FakeFoundCameraCache(number_of_cameras_found=0, cameras_found_list=[])

    class CamGroupQueueProcess:
        @staticmethod
        def _begin(cam_ids, queues, event_dictionary, camera_config_dict):
            return cam_ids, queues, event_dictionary, camera_config_dict

        def _get_queue_by_camera_id(self, camera_id):
            return self._queues[camera_id]

        def get_current_frame_by_camera_id(self, camera_id):
            camera_queue = self._get_queue_by_camera_id(camera_id)
            if not camera_queue.empty():
                return camera_queue.get(block=True)
            return None

    class Camera:
        @property
        def new_frame_ready(self):
            return False

        @property
        def latest_frame(self):
            return self._capture_thread.latest_frame

    class CameraGroup:
        def _resolve_strategy(self, cam_ids):
            return ("original", cam_ids)

    class VideoCaptureThread:
        def _get_next_frame(self):
            return "original-frame-reader"

    class FramePayload:
        def __init__(
            self,
            success=False,
            image=None,
            timestamp_ns=None,
            number_of_frames_received=None,
            number_of_frames_recorded=None,
            camera_id=None,
            mean_frames_per_second=None,
            queue_size=None,
        ):
            self.success = success
            self.image = image
            self.timestamp_ns = timestamp_ns
            self.number_of_frames_received = number_of_frames_received
            self.number_of_frames_recorded = number_of_frames_recorded
            self.camera_id = camera_id
            self.mean_frames_per_second = mean_frames_per_second
            self.queue_size = queue_size

    def original_apply_configuration(cv2_vid_cap, config):
        apply_config_module.original_apply_configuration_calls.append(config.camera_id)

    detect_possible_cameras_module.CAM_CHECK_NUM = 2
    detect_possible_cameras_module.DetectPossibleCameras = DetectPossibleCameras
    found_camera_cache_module.FoundCameraCache = FakeFoundCameraCache
    determine_backend_module.determine_backend = lambda: "FAKE_BACKEND"
    cam_group_queue_process_module.CamGroupQueueProcess = CamGroupQueueProcess
    camera_camera_module.Camera = Camera
    camera_group_module.CameraGroup = CameraGroup
    internal_camera_thread_module.VideoCaptureThread = VideoCaptureThread
    frame_payload_module.FramePayload = FramePayload
    skellycam_module.Camera = Camera
    apply_config_module.original_apply_configuration_calls = []
    apply_config_module.apply_configuration = original_apply_configuration
    internal_camera_thread_module.apply_configuration = original_apply_configuration

    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "skellycam", skellycam_module)
    monkeypatch.setitem(sys.modules, "skellycam.detection", detection_module)
    monkeypatch.setitem(sys.modules, "skellycam.detection.models", detection_models_module)
    monkeypatch.setitem(sys.modules, "skellycam.detection.models.frame_payload", frame_payload_module)
    monkeypatch.setitem(sys.modules, "skellycam.detection.private", private_module)
    monkeypatch.setitem(
        sys.modules,
        "skellycam.detection.private.detect_possible_cameras",
        detect_possible_cameras_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "skellycam.detection.private.found_camera_cache",
        found_camera_cache_module,
    )
    monkeypatch.setitem(sys.modules, "skellycam.opencv", opencv_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.config", config_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.camera", camera_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.camera.camera", camera_camera_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.camera.internal_camera_thread", internal_camera_thread_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.config.apply_config", apply_config_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.config.determine_backend", determine_backend_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.group", group_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.group.camera_group", camera_group_module)
    monkeypatch.setitem(sys.modules, "skellycam.opencv.group.strategies", strategies_module)
    monkeypatch.setitem(
        sys.modules,
        "skellycam.opencv.group.strategies.cam_group_queue_process",
        cam_group_queue_process_module,
    )
    private_module.detect_possible_cameras = detect_possible_cameras_module
    detection_module.models = detection_models_module
    detection_models_module.frame_payload = frame_payload_module
    config_module.apply_config = apply_config_module
    camera_module.internal_camera_thread = internal_camera_thread_module
    camera_module.camera = camera_camera_module
    group_module.camera_group = camera_group_module
    group_module.strategies = strategies_module
    strategies_module.cam_group_queue_process = cam_group_queue_process_module

    return fake_cv2, captures
