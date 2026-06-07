import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SKIP_CONFIG_CAMERA_IDS_ENV_VAR = "FREEMOCAP_SKIP_SKELLYCAM_CONFIG_CAMERA_IDS"
_CAMERA_BACKEND_OVERRIDES_ENV_VAR = "FREEMOCAP_SKELLYCAM_CAMERA_BACKEND_OVERRIDES"
_DIRECTSHOW_YUV_CAMERA_IDS_ENV_VAR = "FREEMOCAP_SKELLYCAM_DIRECTSHOW_YUV_CAMERA_IDS"
_FFMPEG_DIRECTSHOW_CAMERA_IDS_ENV_VAR = "FREEMOCAP_SKELLYCAM_FFMPEG_DIRECTSHOW_CAMERA_IDS"

_PATCH_MARKER_ATTRIBUTE = "_freemocap_accepts_slow_or_static_camera_frames"
_CONFIG_PATCH_MARKER_ATTRIBUTE = "_freemocap_skips_skellycam_config_for_slow_cameras"
_CAPTURE_CREATION_PATCH_MARKER_ATTRIBUTE = "_freemocap_uses_camera_backend_overrides"
_ORIGINAL_CAPTURE_CREATION_ATTRIBUTE = "_freemocap_original_create_cv2_capture"
_CHILD_PROCESS_PATCH_MARKER_ATTRIBUTE = "_freemocap_child_process_camera_patch"
_ORIGINAL_CHILD_PROCESS_BEGIN_ATTRIBUTE = "_freemocap_original_child_process_begin"
_QUEUE_RETRIEVAL_PATCH_MARKER_ATTRIBUTE = "_freemocap_uses_nonblocking_camera_queue_get"
_FRAME_READINESS_PATCH_MARKER_ATTRIBUTE = "_freemocap_uses_frame_count_camera_readiness"
_FRAME_READER_PATCH_MARKER_ATTRIBUTE = "_freemocap_uses_read_for_slow_cameras"
_ORIGINAL_FRAME_READER_ATTRIBUTE = "_freemocap_original_get_next_frame"
_CAMERA_GROUP_STRATEGY_PATCH_MARKER_ATTRIBUTE = "_freemocap_uses_same_process_for_slow_cameras"
_ORIGINAL_CAMERA_GROUP_RESOLVE_ATTRIBUTE = "_freemocap_original_resolve_strategy"
_CHARUCO_OVERLAY_PATCH_MARKER_ATTRIBUTE = "_freemocap_draws_charuco_overlay_on_display_copy"
_ORIGINAL_DRAW_CHARUCO_ATTRIBUTE = "_freemocap_original_draw_charuco_on_image"
_SINGLE_CAMERA_DIAGNOSTICS_PATCH_MARKER_ATTRIBUTE = "_freemocap_single_camera_diagnostics_defaults"
_ORIGINAL_SINGLE_CAMERA_IMAGE_UPDATE_ATTRIBUTE = "_freemocap_original_handle_image_update"
_SAVE_SYNCHRONIZED_CAMERA_IDS_PATCH_MARKER_ATTRIBUTE = "_freemocap_preserves_synchronized_video_camera_ids"
_ORIGINAL_SAVE_SYNCHRONIZED_VIDEOS_ATTRIBUTE = "_freemocap_original_save_synchronized_videos"
_PARAMETER_TREE_USE_CAMERA_PATCH_MARKER_ATTRIBUTE = "_freemocap_honors_use_camera_parameter_values"
_ORIGINAL_PARAMETER_TREE_CONVERT_CAMERA_CONFIG_ATTRIBUTE = "_freemocap_original_convert_camera_config_to_parameter"
_PARAMETER_TREE_ENABLE_SETTINGS_PATCH_MARKER_ATTRIBUTE = "_freemocap_keeps_enabled_camera_settings_editable"
_ORIGINAL_PARAMETER_TREE_ENABLE_SETTINGS_ATTRIBUTE = "_freemocap_original_enable_or_disable_camera_settings"
_CAMERA_UPDATE_CONFIG_STATE_PATCH_MARKER_ATTRIBUTE = "_freemocap_updates_camera_config_state"
_ORIGINAL_CAMERA_UPDATE_CONFIG_ATTRIBUTE = "_freemocap_original_camera_update_config"

_SLOW_CAMERA_FRAME_SECONDS = 0.5
_DROIDCAM_BACKEND_NAME = "MSMF"
_DIRECTSHOW_YUV_FORMAT_GUIDS = {
    "NV12": "{3231564E-0000-0010-8000-00AA00389B71}",
    "I420": "{30323449-0000-0010-8000-00AA00389B71}",
    "YUY2": "{32595559-0000-0010-8000-00AA00389B71}",
}
_FFMPEG_PIXEL_FORMAT_BY_DIRECTSHOW_SUBTYPE = {
    "NV12": "nv12",
    "I420": "yuv420p",
    "YUY2": "yuyv422",
}

_CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG = set()
_CAMERA_BACKEND_OVERRIDES: Dict[str, str] = {}
_CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE = set()
_CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE = set()
_DIRECTSHOW_CAMERA_NAMES_CACHE: Optional[List[str]] = None


def allow_slow_or_static_cameras_in_skellycam_detection() -> None:
    """Patch skellycam camera probing for DroidCam and OBS virtual camera devices."""
    from skellycam.detection.private.detect_possible_cameras import DetectPossibleCameras

    _load_skip_config_camera_ids_from_environment()
    _load_camera_backend_overrides_from_environment()
    _load_directshow_yuv_camera_ids_from_environment()
    _load_ffmpeg_directshow_camera_ids_from_environment()
    _patch_skellycam_camera_configuration()
    _patch_skellycam_camera_update_config_state()
    _patch_skellycam_camera_queue_retrieval()
    _patch_skellycam_capture_creation()
    _patch_skellycam_camera_frame_reader()
    _patch_skellycam_camera_frame_readiness()
    _patch_skellycam_camera_group_strategy()
    _patch_skellycam_camera_child_process_startup()
    _patch_skellycam_charuco_overlay_display_copy()
    _patch_skellycam_single_camera_diagnostics_defaults()
    _patch_skellycam_synchronized_video_camera_ids()
    _patch_skellycam_parameter_tree_use_camera_values()

    current_detector = DetectPossibleCameras.find_available_cameras
    if getattr(current_detector, _PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(_find_available_cameras_accepting_slow_or_static_frames, _PATCH_MARKER_ATTRIBUTE, True)
    DetectPossibleCameras.find_available_cameras = _find_available_cameras_accepting_slow_or_static_frames
    logger.debug("Patched skellycam camera detection for DroidCam/OBS compatibility.")


def _find_available_cameras_accepting_slow_or_static_frames(self: Any):
    import cv2

    from skellycam.detection.private import detect_possible_cameras
    from skellycam.detection.private.found_camera_cache import FoundCameraCache
    from skellycam.opencv.config.determine_backend import determine_backend

    cv2_backend = determine_backend()
    cams_to_use_list = []
    caps_to_release_after_scan = []

    for cam_id in range(detect_possible_cameras.CAM_CHECK_NUM):
        camera_name = _get_directshow_camera_name(cam_id)
        for capture_source in _get_detection_capture_sources(
            default_backend=cv2_backend,
            camera_name=camera_name,
            cv2_module=cv2,
        ):
            cap = None
            try:
                cap = _open_detection_capture(cam_id=cam_id, capture_source=capture_source, cv2_module=cv2)
                if hasattr(cap, "isOpened") and not cap.isOpened():
                    continue

                first_read_start = time.perf_counter()
                success, image = cap.read()
                first_read_duration = time.perf_counter() - first_read_start
                if not _is_valid_frame(success=success, image=image):
                    continue

                second_read_start = time.perf_counter()
                second_success, second_image = cap.read()
                second_read_duration = time.perf_counter() - second_read_start
                slow_camera_frame = (
                    first_read_duration > _SLOW_CAMERA_FRAME_SECONDS
                    or second_read_duration > _SLOW_CAMERA_FRAME_SECONDS
                )

                if (
                    not capture_source.get("allow_identical_frames", False)
                    and not slow_camera_frame
                    and _is_valid_frame(success=second_success, image=second_image)
                    and _is_identical_non_black_frame(image=image, next_image=second_image)
                ):
                    logger.debug(
                        f"Camera {cam_id} appears to return identical non-black frames; "
                        "it is probably a virtual camera, skipping"
                    )
                    continue

                if slow_camera_frame or capture_source.get("skip_skellycam_config", False):
                    _add_skip_config_camera_id(str(cam_id))

                backend_name = capture_source.get("backend_name")
                if backend_name:
                    _add_camera_backend_override(str(cam_id), backend_name)

                if capture_source.get("capture_type") == "directshow_yuv":
                    _add_directshow_yuv_camera_id(str(cam_id))
                elif capture_source.get("capture_type") == "ffmpeg_directshow":
                    _add_ffmpeg_directshow_camera_id(str(cam_id))

                if slow_camera_frame:
                    logger.debug(
                        f"Camera {cam_id} produced a slow frame during detection "
                        f"(first={first_read_duration:.3f}s, second={second_read_duration:.3f}s). "
                        "Allowing it and skipping skellycam's default camera property writes."
                    )

                logger.debug(
                    f"Camera found at port number {cam_id}: success={success}, "
                    f"image.shape={getattr(image, 'shape', None)}, device_name={camera_name}, cap={cap}"
                )
                cams_to_use_list.append(str(cam_id))
                caps_to_release_after_scan.append(cap)
                cap = None
                break
            except Exception as exc:
                logger.error(f"Exception raised when looking for a camera at port {cam_id}: {exc}")
            finally:
                if cap is not None:
                    _release_capture(cap)

    for cap in caps_to_release_after_scan:
        logger.debug(f"Releasing cap {cap}")
        _release_capture(cap)

    logger.info(f"Found cameras: {cams_to_use_list}")
    return FoundCameraCache(
        number_of_cameras_found=len(cams_to_use_list),
        cameras_found_list=cams_to_use_list,
    )


def _get_detection_capture_sources(default_backend: int, camera_name: Optional[str], cv2_module: Any):
    if _is_obs_camera_name(camera_name):
        if _can_use_ffmpeg_directshow_capture():
            return [
                {
                    "capture_type": "ffmpeg_directshow",
                    "skip_skellycam_config": True,
                    "allow_identical_frames": True,
                }
            ]

        logger.warning(
            f"Skipping OBS DirectShow camera ({camera_name}) because imageio-ffmpeg/pygrabber is not available. "
            "OpenCV's DirectShow path produced corrupted frames for this device."
        )
        return []

    sources = []
    msmf_backend = getattr(cv2_module, "CAP_MSMF", None)
    if _is_droidcam_camera_name(camera_name) and sys.platform.startswith("win") and msmf_backend is not None:
        sources.append(
            {
                "capture_type": "opencv",
                "backend": msmf_backend,
                "backend_name": _DROIDCAM_BACKEND_NAME,
                "skip_skellycam_config": True,
                "allow_identical_frames": True,
            }
        )

    sources.append(
        {
            "capture_type": "opencv",
            "backend": default_backend,
            "backend_name": None,
            "skip_skellycam_config": False,
            "allow_identical_frames": False,
        }
    )
    return sources


def _open_detection_capture(cam_id: int, capture_source: Dict[str, Any], cv2_module: Any):
    if capture_source["capture_type"] == "ffmpeg_directshow":
        return _FfmpegDirectShowCameraCapture(camera_id=cam_id)

    if capture_source["capture_type"] == "directshow_yuv":
        return _DirectShowYuvCameraCapture(camera_id=cam_id)

    return cv2_module.VideoCapture(cam_id, capture_source["backend"])


def _get_directshow_camera_name(cam_id: int) -> Optional[str]:
    camera_names = _get_directshow_camera_names()
    if 0 <= cam_id < len(camera_names):
        return camera_names[cam_id]

    return None


def _get_directshow_camera_names() -> List[str]:
    global _DIRECTSHOW_CAMERA_NAMES_CACHE
    if _DIRECTSHOW_CAMERA_NAMES_CACHE is not None:
        return _DIRECTSHOW_CAMERA_NAMES_CACHE

    if not sys.platform.startswith("win"):
        _DIRECTSHOW_CAMERA_NAMES_CACHE = []
        return _DIRECTSHOW_CAMERA_NAMES_CACHE

    try:
        from pygrabber.dshow_graph import FilterGraph

        _DIRECTSHOW_CAMERA_NAMES_CACHE = list(FilterGraph().get_input_devices())
    except Exception as exc:
        logger.debug(f"Could not enumerate DirectShow camera names: {exc}")
        _DIRECTSHOW_CAMERA_NAMES_CACHE = []

    return _DIRECTSHOW_CAMERA_NAMES_CACHE


def _is_droidcam_camera_name(camera_name: Optional[str]) -> bool:
    return camera_name is not None and "droidcam" in camera_name.lower()


def _is_obs_camera_name(camera_name: Optional[str]) -> bool:
    if camera_name is None:
        return False

    normalized_name = camera_name.strip().lower()
    return normalized_name == "obs-camera" or normalized_name.startswith("obs-camera")


def _can_use_directshow_yuv_capture() -> bool:
    if not sys.platform.startswith("win"):
        return False

    try:
        import comtypes  # noqa: F401
        import pygrabber  # noqa: F401
    except Exception:
        return False

    return True


def _can_use_ffmpeg_directshow_capture() -> bool:
    if not sys.platform.startswith("win"):
        return False

    try:
        import imageio_ffmpeg  # noqa: F401
        import pygrabber  # noqa: F401
    except Exception:
        return False

    return True


def _get_ffmpeg_directshow_capture_settings(camera_id: int) -> Dict[str, Any]:
    camera_name = _get_directshow_camera_name(camera_id)
    if camera_name is None:
        raise RuntimeError(f"Could not resolve DirectShow camera name for camera {camera_id}.")

    try:
        from pygrabber.dshow_graph import FilterGraph, FilterType
        from pygrabber.dshow_ids import subtypes

        _register_directshow_yuv_subtypes(subtypes)
        graph = FilterGraph()
        graph.add_video_input_device(camera_id)
        directshow_format = _select_directshow_yuv_format(graph.filters[FilterType.video_input].get_formats())
    except Exception as exc:
        raise RuntimeError(f"Could not inspect DirectShow formats for camera {camera_id}: {exc}") from exc

    if directshow_format is None:
        raise RuntimeError(f"Camera {camera_id} does not expose a supported DirectShow YUV format.")

    subtype = directshow_format["media_type_str"]
    ffmpeg_pixel_format = _FFMPEG_PIXEL_FORMAT_BY_DIRECTSHOW_SUBTYPE.get(subtype)
    if ffmpeg_pixel_format is None:
        raise RuntimeError(f"Camera {camera_id} exposes unsupported DirectShow format {subtype}.")

    framerate = int(round(directshow_format.get("max_framerate") or directshow_format.get("min_framerate") or 30))
    return {
        "camera_name": camera_name,
        "input_width": int(directshow_format["width"]),
        "input_height": int(directshow_format["height"]),
        "framerate": max(framerate, 1),
        "pixel_format": ffmpeg_pixel_format,
    }


class _FfmpegDirectShowCameraCapture:
    def __init__(
        self,
        camera_id: int,
        read_timeout_seconds: float = 1.0,
        output_width: Optional[int] = None,
        output_height: Optional[int] = None,
    ):
        self._camera_id = int(camera_id)
        self._read_timeout_seconds = read_timeout_seconds
        self._condition = threading.Condition()
        self._startup_event = threading.Event()
        self._stop_event = threading.Event()
        self._latest_frame = None
        self._frame_count = 0
        self._opened = False
        self._last_grabbed_frame = None
        self._process = None
        self._stderr_lines = deque(maxlen=50)

        settings = _get_ffmpeg_directshow_capture_settings(self._camera_id)
        self._camera_name = settings["camera_name"]
        self._input_width = settings["input_width"]
        self._input_height = settings["input_height"]
        self._output_width = int(output_width or self._input_width)
        self._output_height = int(output_height or self._input_height)
        self._frame_byte_count = self._output_width * self._output_height * 3

        self._process = self._start_process(settings)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"FfmpegDirectShowCaptureStderr-{self._camera_id}",
            daemon=True,
        )
        self._reader_thread = threading.Thread(
            target=self._read_frames,
            name=f"FfmpegDirectShowCaptureReader-{self._camera_id}",
            daemon=True,
        )
        self._stderr_thread.start()
        self._reader_thread.start()
        self._startup_event.wait(timeout=5.0)
        if not self._opened:
            error_tail = "\n".join(self._stderr_lines)
            self.release()
            raise RuntimeError(
                f"FFmpeg DirectShow capture did not produce a frame for camera {self._camera_id} "
                f"({self._camera_name}). {error_tail}"
            )

    def isOpened(self):
        return self._opened

    def set(self, *_args):
        return False

    def read(self):
        if not self._opened:
            return False, None

        deadline = time.monotonic() + self._read_timeout_seconds
        with self._condition:
            starting_frame_count = self._frame_count
            while self._frame_count == starting_frame_count and self._opened:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    break
                self._condition.wait(remaining_seconds)

            if self._latest_frame is None:
                return False, None

            return True, self._latest_frame.copy()

    def grab(self):
        success, frame = self.read()
        self._last_grabbed_frame = frame if success else None
        return success

    def retrieve(self):
        return self._last_grabbed_frame is not None, self._last_grabbed_frame

    def release(self):
        self._opened = False
        self._stop_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

        for pipe_name in ("stdout", "stderr"):
            pipe = getattr(process, pipe_name, None) if process is not None else None
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

        for thread in (getattr(self, "_reader_thread", None), getattr(self, "_stderr_thread", None)):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def _start_process(self, settings: Dict[str, Any]):
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        command = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "dshow",
            "-video_size",
            f"{self._input_width}x{self._input_height}",
            "-framerate",
            str(settings["framerate"]),
            "-pixel_format",
            settings["pixel_format"],
            "-i",
            f"video={self._camera_name}",
            "-an",
        ]
        if (self._output_width, self._output_height) != (self._input_width, self._input_height):
            command.extend(["-vf", f"scale={self._output_width}:{self._output_height}"])

        command.extend(["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"])
        logger.info(
            f"Starting FFmpeg DirectShow capture for camera {self._camera_id} ({self._camera_name}) "
            f"at {self._output_width}x{self._output_height}."
        )
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _read_frames(self):
        import numpy as np

        try:
            while not self._stop_event.is_set():
                frame_bytes = self._read_exactly(self._frame_byte_count)
                if len(frame_bytes) != self._frame_byte_count:
                    break

                frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((self._output_height, self._output_width, 3))
                self._store_frame(frame.copy())
        finally:
            with self._condition:
                self._opened = False
                self._startup_event.set()
                self._condition.notify_all()

    def _read_exactly(self, byte_count: int) -> bytes:
        stdout = self._process.stdout if self._process is not None else None
        if stdout is None:
            return b""

        chunks = []
        bytes_read = 0
        while bytes_read < byte_count and not self._stop_event.is_set():
            chunk = stdout.read(byte_count - bytes_read)
            if not chunk:
                break
            chunks.append(chunk)
            bytes_read += len(chunk)

        return b"".join(chunks)

    def _drain_stderr(self):
        stderr = self._process.stderr if self._process is not None else None
        if stderr is None:
            return

        for raw_line in iter(stderr.readline, b""):
            if self._stop_event.is_set():
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                self._stderr_lines.append(line)

    def _store_frame(self, frame):
        with self._condition:
            self._latest_frame = frame
            self._frame_count += 1
            self._opened = True
            self._startup_event.set()
            self._condition.notify_all()


class _DirectShowYuvCameraCapture:
    def __init__(self, camera_id: int, read_timeout_seconds: float = 1.0):
        self._camera_id = int(camera_id)
        self._read_timeout_seconds = read_timeout_seconds
        self._condition = threading.Condition()
        self._startup_event = threading.Event()
        self._stop_event = threading.Event()
        self._latest_frame = None
        self._frame_count = 0
        self._graph = None
        self._callback = None
        self._opened = False
        self._last_grabbed_frame = None
        self._startup_exception = None
        self._graph_thread = threading.Thread(
            target=self._run_graph_thread,
            name=f"DirectShowYuvCameraCapture-{self._camera_id}",
            daemon=True,
        )
        self._graph_thread.start()
        self._startup_event.wait(timeout=5.0)
        if self._startup_exception is not None:
            raise self._startup_exception

    def isOpened(self):
        return self._opened

    def set(self, *_args):
        return False

    def read(self):
        if not self._opened:
            return False, None

        deadline = time.monotonic() + self._read_timeout_seconds
        with self._condition:
            starting_frame_count = self._frame_count
            while self._frame_count == starting_frame_count and self._opened:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    break
                self._condition.wait(remaining_seconds)

            if self._latest_frame is None:
                return False, None

            return True, self._latest_frame.copy()

    def grab(self):
        success, frame = self.read()
        self._last_grabbed_frame = frame if success else None
        return success

    def retrieve(self):
        return self._last_grabbed_frame is not None, self._last_grabbed_frame

    def release(self):
        self._opened = False
        self._stop_event.set()
        if self._graph_thread.is_alive():
            self._graph_thread.join(timeout=2.0)
        if self._graph_thread.is_alive():
            logger.debug(f"DirectShow YUV graph thread for camera {self._camera_id} did not stop within timeout.")

    def _run_graph_thread(self):
        import comtypes
        import cv2
        import numpy as np
        from comtypes import COMObject
        from pygrabber.dshow_core import qedit
        from pygrabber.dshow_graph import FilterGraph, FilterType
        from pygrabber.dshow_ids import MediaTypes, subtypes

        graph = None
        com_initialized = False
        try:
            comtypes.CoInitialize()
            com_initialized = True
            _register_directshow_yuv_subtypes(subtypes)

            graph = FilterGraph()
            graph.add_video_input_device(self._camera_id)
            fmt = _select_directshow_yuv_format(graph.filters[FilterType.video_input].get_formats())
            if fmt is None:
                raise RuntimeError(f"Camera {self._camera_id} does not expose a supported YUV DirectShow format.")

            graph.filters[FilterType.video_input].set_format(fmt["index"])
            graph._FilterGraph__add_filter(FilterType.sample_grabber, None)
            sample_grabber = graph.filters[FilterType.sample_grabber]
            sample_grabber.sample_grabber.SetOneShot(0)
            sample_grabber.sample_grabber.SetBufferSamples(0)
            callback_class = _build_yuv_sample_grabber_callback_class(
                qedit=qedit,
                cv2_module=cv2,
                numpy_module=np,
                com_object_class=COMObject,
            )
            self._callback = callback_class(fmt["media_type_str"], self._store_frame)
            sample_grabber.set_media_type(MediaTypes.Video, _DIRECTSHOW_YUV_FORMAT_GUIDS[fmt["media_type_str"]])
            sample_grabber.set_callback(self._callback, 1)
            graph.add_null_render()
            graph.prepare_preview_graph()
            graph.run()

            self._graph = graph
            self._opened = True
            self._startup_event.set()
            while not self._stop_event.is_set():
                try:
                    comtypes.PumpEvents(0.05)
                except AttributeError:
                    time.sleep(0.05)
        except Exception as exc:
            self._startup_exception = exc
            self._startup_event.set()
        finally:
            self._opened = False
            if graph is not None:
                try:
                    graph.stop()
                except Exception as exc:
                    logger.debug(f"Failed to stop DirectShow graph for camera {self._camera_id}: {exc}")
            self._graph = None
            if com_initialized:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def _store_frame(self, frame):
        with self._condition:
            self._latest_frame = frame
            self._frame_count += 1
            self._condition.notify_all()


def _register_directshow_yuv_subtypes(subtypes: Dict[str, str]) -> None:
    for subtype_name, subtype_guid in _DIRECTSHOW_YUV_FORMAT_GUIDS.items():
        subtypes[subtype_guid] = subtype_name
        subtypes[subtype_guid.upper()] = subtype_name


def _select_directshow_yuv_format(formats):
    preferred_subtypes = ("NV12", "I420", "YUY2")
    supported_formats = [fmt for fmt in formats if fmt.get("media_type_str") in preferred_subtypes]
    if not supported_formats:
        return None

    for subtype in preferred_subtypes:
        for fmt in supported_formats:
            if fmt.get("media_type_str") == subtype:
                return fmt

    return supported_formats[0]


def _build_yuv_sample_grabber_callback_class(qedit, cv2_module, numpy_module, com_object_class):
    class _YuvSampleGrabberCallback(com_object_class):
        _com_interfaces_ = [qedit.ISampleGrabberCB]

        def __init__(self, subtype: str, callback):
            self.subtype = subtype
            self.callback = callback
            self.image_resolution = (0, 0)
            super().__init__()

        def SampleCB(self, this, SampleTime, pSample):
            return 0

        def BufferCB(self, this, SampleTime, pBuffer, BufferLen):
            width, height = self.image_resolution
            if not width or not height:
                return 0

            raw = numpy_module.ctypeslib.as_array(pBuffer, shape=(BufferLen,)).copy()
            try:
                if self.subtype == "NV12":
                    yuv = raw[: width * height * 3 // 2].reshape((height * 3 // 2, width))
                    frame = cv2_module.cvtColor(yuv, cv2_module.COLOR_YUV2BGR_NV12)
                elif self.subtype == "I420":
                    yuv = raw[: width * height * 3 // 2].reshape((height * 3 // 2, width))
                    frame = cv2_module.cvtColor(yuv, cv2_module.COLOR_YUV2BGR_I420)
                elif self.subtype == "YUY2":
                    yuv = raw[: width * height * 2].reshape((height, width, 2))
                    frame = cv2_module.cvtColor(yuv, cv2_module.COLOR_YUV2BGR_YUY2)
                else:
                    return 0

                self.callback(frame)
            except Exception as exc:
                logger.debug(f"Failed to convert DirectShow {self.subtype} frame: {exc}")
            return 0

    return _YuvSampleGrabberCallback


def _is_valid_frame(success: bool, image: Any) -> bool:
    return bool(success) and image is not None and getattr(image, "size", 1) > 0


def _is_identical_non_black_frame(image: Any, next_image: Any) -> bool:
    import numpy as np

    return np.mean(next_image) > 10 and np.array_equal(image, next_image)


def _patch_skellycam_camera_configuration() -> None:
    from skellycam.opencv.config import apply_config

    _load_skip_config_camera_ids_from_environment()
    current_apply_configuration = apply_config.apply_configuration
    if getattr(current_apply_configuration, _CONFIG_PATCH_MARKER_ATTRIBUTE, False):
        return

    patched_apply_configuration = _make_apply_configuration_wrapper(current_apply_configuration)
    apply_config.apply_configuration = patched_apply_configuration

    internal_camera_thread = sys.modules.get("skellycam.opencv.camera.internal_camera_thread")
    if internal_camera_thread is not None:
        internal_camera_thread.apply_configuration = patched_apply_configuration

    logger.debug("Patched skellycam camera configuration for DroidCam-style camera feeds.")


def _patch_skellycam_camera_update_config_state() -> None:
    from skellycam.opencv.camera.camera import Camera

    current_update_config = getattr(Camera, "update_config", None)
    if current_update_config is None:
        logger.debug("Could not patch skellycam camera config state; Camera.update_config is unavailable.")
        return

    if getattr(current_update_config, _CAMERA_UPDATE_CONFIG_STATE_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(Camera, _ORIGINAL_CAMERA_UPDATE_CONFIG_ATTRIBUTE, current_update_config)
    setattr(_camera_update_config_storing_config_state, _CAMERA_UPDATE_CONFIG_STATE_PATCH_MARKER_ATTRIBUTE, True)
    Camera.update_config = _camera_update_config_storing_config_state
    logger.debug("Patched skellycam camera updates to store use-camera config state.")


def _camera_update_config_storing_config_state(self: Any, camera_config: Any):
    self._config = camera_config
    original_update_config = getattr(type(self), _ORIGINAL_CAMERA_UPDATE_CONFIG_ATTRIBUTE)
    return original_update_config(self, camera_config)


def _patch_skellycam_capture_creation() -> None:
    from skellycam.opencv.camera.internal_camera_thread import VideoCaptureThread

    current_create_capture = getattr(VideoCaptureThread, "_create_cv2_capture", None)
    if current_create_capture is None or getattr(
        current_create_capture, _CAPTURE_CREATION_PATCH_MARKER_ATTRIBUTE, False
    ):
        return

    setattr(VideoCaptureThread, _ORIGINAL_CAPTURE_CREATION_ATTRIBUTE, current_create_capture)
    setattr(_create_cv2_capture_with_overrides, _CAPTURE_CREATION_PATCH_MARKER_ATTRIBUTE, True)
    VideoCaptureThread._create_cv2_capture = _create_cv2_capture_with_overrides
    logger.debug("Patched skellycam camera capture creation for backend overrides.")


def _create_cv2_capture_with_overrides(self: Any):
    _load_camera_backend_overrides_from_environment()
    _load_directshow_yuv_camera_ids_from_environment()
    _load_ffmpeg_directshow_camera_ids_from_environment()
    camera_id = str(self._config.camera_id)
    _mark_compatibility_capture_for_camera_id(camera_id)

    if camera_id in _CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE:
        return _create_capture_for_thread(
            camera_thread=self,
            capture_factory=lambda: _FfmpegDirectShowCameraCapture(
                int(camera_id),
                output_width=getattr(self._config, "resolution_width", None),
                output_height=getattr(self._config, "resolution_height", None),
            ),
            label="FFmpeg DirectShow",
        )

    if camera_id in _CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE:
        return _create_capture_for_thread(
            camera_thread=self,
            capture_factory=lambda: _DirectShowYuvCameraCapture(int(camera_id)),
            label="DirectShow YUV",
        )

    backend_name = _CAMERA_BACKEND_OVERRIDES.get(camera_id)
    if backend_name is not None:
        import cv2

        return _create_capture_for_thread(
            camera_thread=self,
            capture_factory=lambda: cv2.VideoCapture(int(camera_id), _backend_name_to_cv2_backend(backend_name, cv2)),
            label=f"OpenCV {backend_name}",
        )

    original_create_capture = getattr(type(self), _ORIGINAL_CAPTURE_CREATION_ATTRIBUTE)
    return original_create_capture(self)


def _create_capture_for_thread(camera_thread: Any, capture_factory, label: str):
    from skellycam.opencv.camera import internal_camera_thread

    camera_id = str(camera_thread._config.camera_id)
    logger.info(f"Connecting to Camera: {camera_id} with {label}...")

    existing_capture = getattr(camera_thread, "_cv2_video_capture", None)
    if existing_capture is not None:
        _release_capture(existing_capture)

    capture = capture_factory()
    success = False
    image = None
    for _ in range(3):
        success, image = capture.read()
        if _is_valid_frame(success=success, image=image):
            break

    if not _is_valid_frame(success=success, image=image):
        _release_capture(capture)
        raise RuntimeError(f"Failed to read a frame from camera {camera_id} using {label}.")

    internal_camera_thread.apply_configuration(capture, camera_thread._config)

    logger.info(f"Successfully connected to Camera: {camera_id} with {label}!")
    if not camera_thread._ready_event.is_set():
        camera_thread._ready_event.set()

    return capture


def _backend_name_to_cv2_backend(backend_name: str, cv2_module: Any):
    if backend_name == _DROIDCAM_BACKEND_NAME:
        return cv2_module.CAP_MSMF

    raise ValueError(f"Unsupported camera backend override: {backend_name}")


def _patch_skellycam_camera_child_process_startup() -> None:
    from skellycam.opencv.group.strategies.cam_group_queue_process import CamGroupQueueProcess

    current_begin = CamGroupQueueProcess._begin
    if getattr(current_begin, _CHILD_PROCESS_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(CamGroupQueueProcess, _ORIGINAL_CHILD_PROCESS_BEGIN_ATTRIBUTE, current_begin)
    setattr(_cam_group_queue_process_begin_with_child_patches, _CHILD_PROCESS_PATCH_MARKER_ATTRIBUTE, True)
    CamGroupQueueProcess._begin = staticmethod(_cam_group_queue_process_begin_with_child_patches)
    logger.debug("Patched skellycam camera child process startup.")


def _patch_skellycam_charuco_overlay_display_copy() -> None:
    try:
        from skellycam.gui.qt.workers import camera_group_thread_worker
    except ImportError as exc:
        logger.debug(f"Could not patch skellycam Charuco overlay display copy: {exc}")
        return

    CamGroupThreadWorker = camera_group_thread_worker.CamGroupThreadWorker
    current_convert_frame = CamGroupThreadWorker._convert_frame
    if getattr(current_convert_frame, _CHARUCO_OVERLAY_PATCH_MARKER_ATTRIBUTE, False):
        return

    if not hasattr(camera_group_thread_worker, _ORIGINAL_DRAW_CHARUCO_ATTRIBUTE):
        setattr(
            camera_group_thread_worker,
            _ORIGINAL_DRAW_CHARUCO_ATTRIBUTE,
            camera_group_thread_worker.draw_charuco_on_image,
        )

    camera_group_thread_worker.draw_charuco_on_image = _do_not_mutate_recorded_frame_with_charuco_overlay
    setattr(_convert_frame_with_display_only_charuco_overlay, _CHARUCO_OVERLAY_PATCH_MARKER_ATTRIBUTE, True)
    CamGroupThreadWorker._convert_frame = _convert_frame_with_display_only_charuco_overlay
    logger.debug("Patched skellycam Charuco overlay to draw only on display copies.")


def _do_not_mutate_recorded_frame_with_charuco_overlay(*_args, **_kwargs) -> None:
    return None


def _convert_frame_with_display_only_charuco_overlay(self: Any, frame: Any):
    import cv2

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from skellycam.gui.qt.workers import camera_group_thread_worker

    image = frame.image
    if getattr(self, "annotate_images", False):
        image = image.copy()
        original_draw_charuco = getattr(camera_group_thread_worker, _ORIGINAL_DRAW_CHARUCO_ATTRIBUTE)
        original_draw_charuco(image=image, charuco_board=self.charuco_board)

    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    converted_frame = QImage(
        image.data,
        image.shape[1],
        image.shape[0],
        QImage.Format.Format_RGB888,
    )

    return converted_frame.scaled(
        int(image.shape[1] / 2),
        int(image.shape[0] / 2),
        Qt.AspectRatioMode.KeepAspectRatio,
    )


def _patch_skellycam_single_camera_diagnostics_defaults() -> None:
    try:
        from skellycam.gui.qt.widgets.single_camera_view_widget import SingleCameraViewWidget
    except ImportError as exc:
        logger.debug(f"Could not patch skellycam single camera diagnostics defaults: {exc}")
        return

    current_handle_image_update = SingleCameraViewWidget.handle_image_update
    if getattr(current_handle_image_update, _SINGLE_CAMERA_DIAGNOSTICS_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(SingleCameraViewWidget, _ORIGINAL_SINGLE_CAMERA_IMAGE_UPDATE_ATTRIBUTE, current_handle_image_update)
    setattr(_handle_image_update_with_diagnostic_defaults, _SINGLE_CAMERA_DIAGNOSTICS_PATCH_MARKER_ATTRIBUTE, True)
    SingleCameraViewWidget.handle_image_update = _handle_image_update_with_diagnostic_defaults
    logger.debug("Patched skellycam single camera view diagnostics defaults.")


def _handle_image_update_with_diagnostic_defaults(self: Any, q_image: Any, frame_diagnostics_dictionary: Dict):
    original_handle_image_update = getattr(type(self), _ORIGINAL_SINGLE_CAMERA_IMAGE_UPDATE_ATTRIBUTE)
    diagnostics = dict(frame_diagnostics_dictionary or {})
    diagnostics.setdefault("queue_size", 0)
    diagnostics.setdefault("frames_recorded", 0)
    return original_handle_image_update(self, q_image, diagnostics)


def _patch_skellycam_synchronized_video_camera_ids() -> None:
    try:
        from skellycam.gui.qt.workers import video_save_thread_worker
        from skellycam.opencv.video_recorder import save_synchronized_videos
    except ImportError as exc:
        logger.debug(f"Could not patch skellycam synchronized video camera IDs: {exc}")
        return

    current_save_synchronized_videos = save_synchronized_videos.save_synchronized_videos
    if getattr(current_save_synchronized_videos, _SAVE_SYNCHRONIZED_CAMERA_IDS_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(
        save_synchronized_videos,
        _ORIGINAL_SAVE_SYNCHRONIZED_VIDEOS_ATTRIBUTE,
        current_save_synchronized_videos,
    )
    setattr(
        _save_synchronized_videos_preserving_camera_ids,
        _SAVE_SYNCHRONIZED_CAMERA_IDS_PATCH_MARKER_ATTRIBUTE,
        True,
    )
    save_synchronized_videos.save_synchronized_videos = _save_synchronized_videos_preserving_camera_ids
    video_save_thread_worker.save_synchronized_videos = _save_synchronized_videos_preserving_camera_ids
    logger.debug("Patched skellycam synchronized video saving to preserve physical camera IDs.")


def _save_synchronized_videos_preserving_camera_ids(
    dictionary_of_video_recorders: Dict[str, Any],
    folder_to_save_videos: Any,
    create_diagnostic_plots_bool: bool = True,
) -> None:
    import platform
    from pathlib import Path

    import numpy as np

    from skellycam.diagnostics.create_diagnostic_plots import create_diagnostic_plots
    from skellycam.opencv.video_recorder.save_synchronized_videos import get_nearest_frame
    from skellycam.opencv.video_recorder.video_recorder import VideoRecorder
    from skellycam.tests.test_frame_timestamp_synchronization import test_frame_timestamp_synchronization
    from skellycam.tests.test_synchronized_video_frame_counts import test_synchronized_video_frame_counts

    logger.info(f"Saving synchronized videos to folder: {str(folder_to_save_videos)}")

    video_recorder_items = [
        (str(camera_id), video_recorder)
        for camera_id, video_recorder in dictionary_of_video_recorders.items()
        if video_recorder.number_of_frames > 0
    ]
    if not video_recorder_items:
        logger.warning("No recorded frames were available to save.")
        return

    camera_ids = [camera_id for camera_id, _video_recorder in video_recorder_items]
    each_cam_raw_frame_list = []
    first_frame_timestamps = []
    final_frame_timestamps = []

    for camera_id, video_recorder in video_recorder_items:
        camera_frame_list = video_recorder.frame_payload_list
        first_frame_timestamps.append(camera_frame_list[0].timestamp_ns)
        final_frame_timestamps.append(camera_frame_list[-1].timestamp_ns)
        each_cam_raw_frame_list.append(camera_frame_list)
        logger.info(f"Camera {camera_id} has {len(camera_frame_list)} raw recorded frames.")

    latest_first_frame = np.max(first_frame_timestamps)
    earliest_final_frame = np.min(final_frame_timestamps)

    logger.info(f"first_frame_timestamps: {first_frame_timestamps}")
    logger.info(f"np.diff(first_frame_timestamps): {np.diff(first_frame_timestamps)}")
    logger.info(f"latest_first_frame: {latest_first_frame}")
    logger.info(f"final_frame_timestamps: {final_frame_timestamps}")
    logger.info(f"np.diff(final_frame_timestamps): {np.diff(final_frame_timestamps)}")
    logger.info(f"earliest_final_frame: {earliest_final_frame}")
    logger.info("Clipping each camera's frame list to latest first frame and earliest final frame")

    each_cam_clipped_frame_list = []
    each_cam_clipped_timestamp_list = []
    for og_frame_list in each_cam_raw_frame_list:
        each_cam_clipped_frame_list.append([])
        each_cam_clipped_timestamp_list.append([])
        for frame in og_frame_list:
            if frame.timestamp_ns < latest_first_frame:
                continue
            if frame.timestamp_ns > earliest_final_frame:
                continue

            each_cam_clipped_frame_list[-1].append(frame)
            each_cam_clipped_timestamp_list[-1].append(frame.timestamp_ns)

    number_of_frames_per_camera_clipped = [len(frame_list) for frame_list in each_cam_clipped_frame_list]
    min_number_of_frames = np.min(number_of_frames_per_camera_clipped)
    index_of_the_camera_with_fewest_frames = np.argmin(number_of_frames_per_camera_clipped)
    reference_frame_list = each_cam_clipped_frame_list[index_of_the_camera_with_fewest_frames]
    logger.info(
        f"(clipped) number_of_frames_per_camera: {number_of_frames_per_camera_clipped}, " f"min:{min_number_of_frames}"
    )
    logger.info(
        "Creating synchronized frame list by matching each camera's timestamps to the timestamps of the "
        "camera with the fewest frames"
    )

    synchronized_frame_list_dictionary = {}
    for camera_id, camera_frame_list in zip(camera_ids, each_cam_clipped_frame_list):
        logger.info(f"Creating synchronized frame list for physical camera {camera_id}...")
        cam_synchronized_frame_list = []
        for reference_frame in reference_frame_list:
            closest_frame = get_nearest_frame(camera_frame_list, reference_frame)
            cam_synchronized_frame_list.append(closest_frame)
        synchronized_frame_list_dictionary[str(camera_id)] = cam_synchronized_frame_list

    test_frame_timestamp_synchronization(synchronized_frame_list_dictionary=synchronized_frame_list_dictionary)

    Path(folder_to_save_videos).mkdir(parents=True, exist_ok=True)
    for camera_id, frame_list in synchronized_frame_list_dictionary.items():
        logger.info(f"Saving physical camera {camera_id} video with {len(frame_list)} frames...")
        VideoRecorder().save_frame_list_to_video_file(
            frame_payload_list=frame_list,
            video_file_save_path=Path(folder_to_save_videos) / f"Camera_{str(camera_id).zfill(3)}_synchronized.mp4",
        )

    test_synchronized_video_frame_counts(video_folder_path=folder_to_save_videos)

    if platform.system() != "Windows":
        logger.info("Non-Windows system detected, diagnostic plots for webcams will not be displayed")
        logger.info("Done!")
        return

    if create_diagnostic_plots_bool:
        create_diagnostic_plots(
            video_recorder_dictionary=dictionary_of_video_recorders,
            synchronized_frame_list_dictionary=synchronized_frame_list_dictionary,
            folder_to_save_plots=folder_to_save_videos,
            show_plots_bool=True,
        )

    logger.info("Done!")


def _patch_skellycam_parameter_tree_use_camera_values() -> None:
    try:
        from skellycam.gui.qt.widgets import skelly_cam_config_parameter_tree_widget
    except ImportError as exc:
        logger.debug(f"Could not patch skellycam camera parameter tree values: {exc}")
        return

    SkellyCamParameterTreeWidget = skelly_cam_config_parameter_tree_widget.SkellyCamParameterTreeWidget
    current_enable_or_disable_settings = SkellyCamParameterTreeWidget._enable_or_disable_camera_settings
    if not getattr(
        current_enable_or_disable_settings,
        _PARAMETER_TREE_ENABLE_SETTINGS_PATCH_MARKER_ATTRIBUTE,
        False,
    ):
        setattr(
            SkellyCamParameterTreeWidget,
            _ORIGINAL_PARAMETER_TREE_ENABLE_SETTINGS_ATTRIBUTE,
            current_enable_or_disable_settings,
        )
        setattr(
            _enable_or_disable_camera_settings_with_editable_active_cameras,
            _PARAMETER_TREE_ENABLE_SETTINGS_PATCH_MARKER_ATTRIBUTE,
            True,
        )
        SkellyCamParameterTreeWidget._enable_or_disable_camera_settings = (
            _enable_or_disable_camera_settings_with_editable_active_cameras
        )

    current_convert_camera_config = SkellyCamParameterTreeWidget._convert_camera_config_to_parameter
    if getattr(current_convert_camera_config, _PARAMETER_TREE_USE_CAMERA_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(
        SkellyCamParameterTreeWidget,
        _ORIGINAL_PARAMETER_TREE_CONVERT_CAMERA_CONFIG_ATTRIBUTE,
        current_convert_camera_config,
    )
    setattr(
        _convert_camera_config_to_parameter_honoring_use_camera,
        _PARAMETER_TREE_USE_CAMERA_PATCH_MARKER_ATTRIBUTE,
        True,
    )
    SkellyCamParameterTreeWidget._convert_camera_config_to_parameter = (
        _convert_camera_config_to_parameter_honoring_use_camera
    )
    logger.debug("Patched skellycam camera parameter tree to honor and immediately apply use-camera values.")


def _enable_or_disable_camera_settings_with_editable_active_cameras(
    self: Any, camera_config_parameter_group: Any
) -> None:
    from skellycam.gui.qt.utilities.qt_label_strings import USE_THIS_CAMERA_STRING

    use_this_camera_checked = camera_config_parameter_group.param(USE_THIS_CAMERA_STRING).value()
    for child_parameter in camera_config_parameter_group.children():
        if child_parameter.name() == USE_THIS_CAMERA_STRING:
            continue

        child_parameter.setOpts(enabled=use_this_camera_checked)
        try:
            child_parameter.setReadonly(not use_this_camera_checked)
        except AttributeError:
            pass


def _convert_camera_config_to_parameter_honoring_use_camera(self: Any, camera_config: Any):
    from skellycam.gui.qt.utilities.qt_label_strings import USE_THIS_CAMERA_STRING

    original_convert_camera_config = getattr(type(self), _ORIGINAL_PARAMETER_TREE_CONVERT_CAMERA_CONFIG_ATTRIBUTE)
    camera_parameter_group = original_convert_camera_config(self, camera_config)

    try:
        use_this_camera_parameter = camera_parameter_group.param(USE_THIS_CAMERA_STRING)
        use_this_camera_parameter.setValue(bool(camera_config.use_this_camera))
        self._enable_or_disable_camera_settings(camera_parameter_group)
        use_this_camera_parameter.sigValueChanged.connect(lambda *_args: self._emit_camera_configs_dict())
    except Exception as exc:
        logger.debug(f"Could not initialize use-camera parameter for camera {camera_config.camera_id}: {exc}")

    return camera_parameter_group


def _cam_group_queue_process_begin_with_child_patches(cam_ids, queues, event_dictionary, camera_config_dict):
    _patch_skellycam_camera_configuration()
    _patch_skellycam_camera_update_config_state()
    _patch_skellycam_capture_creation()
    _patch_skellycam_camera_frame_reader()
    _patch_skellycam_camera_frame_readiness()
    _patch_skellycam_charuco_overlay_display_copy()
    _patch_skellycam_single_camera_diagnostics_defaults()

    from skellycam.opencv.group.strategies.cam_group_queue_process import CamGroupQueueProcess

    original_begin = getattr(CamGroupQueueProcess, _ORIGINAL_CHILD_PROCESS_BEGIN_ATTRIBUTE, None)
    if original_begin is not None:
        return original_begin(cam_ids, queues, event_dictionary, camera_config_dict)

    return CamGroupQueueProcess._begin(cam_ids, queues, event_dictionary, camera_config_dict)


def _patch_skellycam_camera_queue_retrieval() -> None:
    from skellycam.opencv.group.strategies.cam_group_queue_process import CamGroupQueueProcess

    current_get_frame = CamGroupQueueProcess.get_current_frame_by_camera_id
    if getattr(current_get_frame, _QUEUE_RETRIEVAL_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(_get_current_frame_by_camera_id_without_empty_check, _QUEUE_RETRIEVAL_PATCH_MARKER_ATTRIBUTE, True)
    CamGroupQueueProcess.get_current_frame_by_camera_id = _get_current_frame_by_camera_id_without_empty_check
    logger.debug("Patched skellycam camera queue retrieval to avoid multiprocessing Queue.empty().")


def _get_current_frame_by_camera_id_without_empty_check(self: Any, camera_id: str):
    try:
        if camera_id not in self._queues:
            return None

        camera_queue = self._get_queue_by_camera_id(camera_id)
        return camera_queue.get(block=False)
    except queue.Empty:
        return None
    except Exception as exc:
        logger.exception(f"Problem when grabbing a frame from: Camera {camera_id} - {exc}")
        return None


def _patch_skellycam_camera_group_strategy() -> None:
    from skellycam.opencv.group.camera_group import CameraGroup

    current_resolve_strategy = CameraGroup._resolve_strategy
    if getattr(current_resolve_strategy, _CAMERA_GROUP_STRATEGY_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(CameraGroup, _ORIGINAL_CAMERA_GROUP_RESOLVE_ATTRIBUTE, current_resolve_strategy)
    setattr(_resolve_strategy_using_same_process_for_slow_cameras, _CAMERA_GROUP_STRATEGY_PATCH_MARKER_ATTRIBUTE, True)
    CameraGroup._resolve_strategy = _resolve_strategy_using_same_process_for_slow_cameras
    logger.debug("Patched skellycam camera group strategy for DroidCam-style camera feeds.")


def _resolve_strategy_using_same_process_for_slow_cameras(self: Any, cam_ids: List[str]):
    _load_skip_config_camera_ids_from_environment()
    camera_ids = [str(camera_id) for camera_id in cam_ids]
    for camera_id in camera_ids:
        _mark_compatibility_capture_for_camera_id(camera_id)

    if any(camera_id in _CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG for camera_id in camera_ids):
        logger.info(
            "Using same-process skellycam camera strategy for DroidCam/OBS cameras "
            f"{camera_ids} to avoid Windows multiprocessing manager queues."
        )
        return _SameProcessCameraStrategy(camera_ids)

    original_resolve_strategy = getattr(type(self), _ORIGINAL_CAMERA_GROUP_RESOLVE_ATTRIBUTE)
    return original_resolve_strategy(self, cam_ids)


class _SameProcessCameraStrategy:
    def __init__(self, camera_ids: List[str]):
        self._camera_ids = [str(camera_id) for camera_id in camera_ids]
        self._cameras = {}
        self._ready_event_dictionary = {}
        self._exit_event = None
        self._closed = False

    @property
    def processes(self):
        return []

    @property
    def is_capturing(self) -> bool:
        if self._exit_event is not None and self._exit_event.is_set():
            self._close_cameras()
            return False

        if not self._cameras:
            return False

        return any(
            _camera_config_uses_this_camera(camera) and camera.is_capturing_frames for camera in self._cameras.values()
        )

    @property
    def queue_size(self) -> Dict[str, int]:
        return {
            camera_id: int(
                self._cameras.get(camera_id) is not None
                and _camera_config_uses_this_camera(self._cameras[camera_id])
                and self._cameras[camera_id].new_frame_ready
            )
            for camera_id in self._camera_ids
        }

    def start_capture(
        self,
        event_dictionary: Dict[str, multiprocessing.Event],
        camera_config_dict: Dict[str, Any],
    ) -> None:
        from skellycam import Camera

        self._exit_event = event_dictionary["exit"]
        self._ready_event_dictionary = {camera_id: multiprocessing.Event() for camera_id in self._camera_ids}
        event_dictionary["ready"] = self._ready_event_dictionary

        self._cameras = {}
        for camera_id in self._camera_ids:
            camera = Camera(camera_config_dict[camera_id])
            self._cameras[camera_id] = camera
            camera.connect(self._ready_event_dictionary[camera_id])

    def check_if_camera_is_ready(self, cam_id: str) -> bool:
        ready_event = self._ready_event_dictionary.get(str(cam_id))
        return ready_event is not None and ready_event.is_set()

    def get_current_frame_by_cam_id(self, camera_id: str):
        return self.get_current_frame_by_camera_id(camera_id)

    def get_current_frame_by_camera_id(self, camera_id: str):
        camera = self._cameras.get(str(camera_id))
        if camera is None or not _camera_config_uses_this_camera(camera) or not camera.new_frame_ready:
            return None

        return camera.latest_frame

    def get_latest_frames(self):
        return {
            camera_id: self.get_current_frame_by_camera_id(camera_id)
            for camera_id in self._camera_ids
            if self._cameras.get(camera_id) is not None and _camera_config_uses_this_camera(self._cameras[camera_id])
        }

    def update_camera_configs(self, camera_config_dictionary: Dict[str, Any]) -> None:
        for camera_id, camera in self._cameras.items():
            if camera_id in camera_config_dictionary:
                camera._config = camera_config_dictionary[camera_id]
                camera.update_config(camera_config_dictionary[camera_id])

    def _close_cameras(self) -> None:
        if self._closed:
            return

        for camera in self._cameras.values():
            camera.close()
        self._closed = True


def _camera_config_uses_this_camera(camera: Any) -> bool:
    config = getattr(camera, "_config", None)
    return bool(getattr(config, "use_this_camera", True))


def _patch_skellycam_camera_frame_readiness() -> None:
    from skellycam.opencv.camera.camera import Camera

    current_new_frame_ready = Camera.new_frame_ready
    if getattr(current_new_frame_ready.fget, _FRAME_READINESS_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(_camera_new_frame_ready_from_frame_count, _FRAME_READINESS_PATCH_MARKER_ATTRIBUTE, True)
    setattr(_camera_latest_frame_tracking_frame_count, _FRAME_READINESS_PATCH_MARKER_ATTRIBUTE, True)
    Camera.new_frame_ready = property(_camera_new_frame_ready_from_frame_count)
    Camera.latest_frame = property(_camera_latest_frame_tracking_frame_count)
    logger.debug("Patched skellycam camera frame readiness to track received frame counts.")


def _patch_skellycam_camera_frame_reader() -> None:
    from skellycam.opencv.camera.internal_camera_thread import VideoCaptureThread

    current_get_next_frame = VideoCaptureThread._get_next_frame
    if getattr(current_get_next_frame, _FRAME_READER_PATCH_MARKER_ATTRIBUTE, False):
        return

    setattr(VideoCaptureThread, _ORIGINAL_FRAME_READER_ATTRIBUTE, current_get_next_frame)
    setattr(_get_next_frame_using_read_for_slow_cameras, _FRAME_READER_PATCH_MARKER_ATTRIBUTE, True)
    VideoCaptureThread._get_next_frame = _get_next_frame_using_read_for_slow_cameras
    logger.debug("Patched skellycam camera frame reader for DroidCam-style camera feeds.")


def _get_next_frame_using_read_for_slow_cameras(self: Any):
    _load_skip_config_camera_ids_from_environment()
    camera_id = str(self._config.camera_id)
    if camera_id not in _CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG:
        original_get_next_frame = getattr(type(self), _ORIGINAL_FRAME_READER_ATTRIBUTE)
        return original_get_next_frame(self)

    import cv2

    from skellycam.detection.models.frame_payload import FramePayload

    success = False
    image = None
    retrieval_timestamp = time.perf_counter_ns()
    try:
        success, image = self._cv2_video_capture.read()
        retrieval_timestamp = time.perf_counter_ns()
        success = _is_valid_frame(success=success, image=image)
        if success and self._config.rotate_video_cv2_code != -1:
            image = cv2.rotate(image, self._config.rotate_video_cv2_code)
    except Exception as exc:
        logger.error(f"Failed to read frame from Camera: {camera_id}: {exc}")
        success = False
        image = None

    self._new_frame_ready = success
    if success:
        self._number_of_frames_received += 1

    return FramePayload(
        success=success,
        image=image,
        timestamp_ns=retrieval_timestamp,
        number_of_frames_received=self._number_of_frames_received,
        camera_id=camera_id,
    )


def _camera_new_frame_ready_from_frame_count(self: Any) -> bool:
    capture_thread = getattr(self, "_capture_thread", None)
    frame_payload = getattr(capture_thread, "_frame", None)
    if not _is_queueable_frame_payload(frame_payload):
        return False

    current_frame_count = frame_payload.number_of_frames_received
    last_returned_frame_count = getattr(self, "_freemocap_last_returned_frame_count", None)
    return current_frame_count != last_returned_frame_count


def _camera_latest_frame_tracking_frame_count(self: Any):
    frame_payload = self._capture_thread.latest_frame
    if frame_payload is not None:
        self._freemocap_last_returned_frame_count = frame_payload.number_of_frames_received
    return frame_payload


def _is_queueable_frame_payload(frame_payload: Any) -> bool:
    if frame_payload is None:
        return False

    return _is_valid_frame(
        success=getattr(frame_payload, "success", False), image=getattr(frame_payload, "image", None)
    )


def _make_apply_configuration_wrapper(original_apply_configuration):
    def _apply_configuration_skipping_slow_cameras(cv2_vid_cap, config):
        camera_id = str(config.camera_id)
        if camera_id in _CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG:
            logger.info(
                f"Skipping skellycam default camera property writes for camera {camera_id}; "
                "it behaved like a DroidCam/OBS feed during detection."
            )
            return

        return original_apply_configuration(cv2_vid_cap, config)

    setattr(_apply_configuration_skipping_slow_cameras, _CONFIG_PATCH_MARKER_ATTRIBUTE, True)
    return _apply_configuration_skipping_slow_cameras


def _add_skip_config_camera_id(camera_id: str) -> None:
    _CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG.add(camera_id)
    os.environ[_SKIP_CONFIG_CAMERA_IDS_ENV_VAR] = ",".join(sorted(_CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG))


def _mark_compatibility_capture_for_camera_id(camera_id: str) -> bool:
    try:
        camera_index = int(camera_id)
    except ValueError:
        return False

    camera_name = _get_directshow_camera_name(camera_index)
    if _is_obs_camera_name(camera_name):
        if _can_use_ffmpeg_directshow_capture():
            _add_skip_config_camera_id(camera_id)
            _add_ffmpeg_directshow_camera_id(camera_id)
            return True

        logger.warning(
            f"OBS DirectShow camera {camera_id} ({camera_name}) needs imageio-ffmpeg/pygrabber for reliable capture."
        )
        return False

    if _is_droidcam_camera_name(camera_name) and sys.platform.startswith("win"):
        _add_skip_config_camera_id(camera_id)
        _add_camera_backend_override(camera_id, _DROIDCAM_BACKEND_NAME)
        return True

    return False


def _load_skip_config_camera_ids_from_environment() -> None:
    camera_ids_string = os.environ.get(_SKIP_CONFIG_CAMERA_IDS_ENV_VAR, "")
    if camera_ids_string:
        _CAMERA_IDS_TO_SKIP_SKELLYCAM_CONFIG.update(
            camera_id.strip() for camera_id in camera_ids_string.split(",") if camera_id.strip()
        )


def _add_camera_backend_override(camera_id: str, backend_name: str) -> None:
    _CAMERA_BACKEND_OVERRIDES[camera_id] = backend_name
    os.environ[_CAMERA_BACKEND_OVERRIDES_ENV_VAR] = ",".join(
        f"{saved_camera_id}:{saved_backend_name}"
        for saved_camera_id, saved_backend_name in sorted(_CAMERA_BACKEND_OVERRIDES.items())
    )


def _load_camera_backend_overrides_from_environment() -> None:
    overrides_string = os.environ.get(_CAMERA_BACKEND_OVERRIDES_ENV_VAR, "")
    if not overrides_string:
        return

    for override in overrides_string.split(","):
        if ":" not in override:
            continue
        camera_id, backend_name = override.split(":", 1)
        camera_id = camera_id.strip()
        backend_name = backend_name.strip()
        if camera_id and backend_name:
            _CAMERA_BACKEND_OVERRIDES[camera_id] = backend_name


def _add_directshow_yuv_camera_id(camera_id: str) -> None:
    _CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE.add(camera_id)
    os.environ[_DIRECTSHOW_YUV_CAMERA_IDS_ENV_VAR] = ",".join(sorted(_CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE))


def _load_directshow_yuv_camera_ids_from_environment() -> None:
    camera_ids_string = os.environ.get(_DIRECTSHOW_YUV_CAMERA_IDS_ENV_VAR, "")
    if camera_ids_string:
        _CAMERA_IDS_TO_USE_DIRECTSHOW_YUV_CAPTURE.update(
            camera_id.strip() for camera_id in camera_ids_string.split(",") if camera_id.strip()
        )


def _add_ffmpeg_directshow_camera_id(camera_id: str) -> None:
    _CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE.add(camera_id)
    os.environ[_FFMPEG_DIRECTSHOW_CAMERA_IDS_ENV_VAR] = ",".join(sorted(_CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE))


def _load_ffmpeg_directshow_camera_ids_from_environment() -> None:
    camera_ids_string = os.environ.get(_FFMPEG_DIRECTSHOW_CAMERA_IDS_ENV_VAR, "")
    if camera_ids_string:
        _CAMERA_IDS_TO_USE_FFMPEG_DIRECTSHOW_CAPTURE.update(
            camera_id.strip() for camera_id in camera_ids_string.split(",") if camera_id.strip()
        )


def _release_capture(cap: Any) -> None:
    try:
        cap.release()
    except Exception as exc:
        logger.debug(f"Failed to release camera capture {cap}: {exc}")
