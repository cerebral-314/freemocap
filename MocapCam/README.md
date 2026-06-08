# MocapCam

MocapCam is the first iOS client for the FreeMoCap Capture Link plan. It turns an iPhone or iPad into a mocap-native RGB capture device instead of a generic webcam replacement.

This implementation covers the first end-to-end Capture Link prototype from `freemocap_lidar_wifi_mocap_plan.md`:

- rear RGB capture through `AVFoundation`
- automatic LiDAR/depth capture through `AVCaptureDepthDataOutput` when supported
- live false-color LiDAR overlay on the iOS camera feed
- H.264 hardware encoding through `VideoToolbox`
- Bonjour advertisement on `_mocapcam._tcp`
- TCP control channel with newline-delimited JSON commands
- binary frame packets with JSON metadata plus encoded video payloads
- binary depth packets encoded as `uint16_mm_little_endian`
- local MOV/depth recording with manifests
- local file manifest/download recovery after capture
- device-motion recording to `device_motion.json`
- synchronized resolution, FPS, exposure, focus, and white-balance settings for calibration/takes
- live device status for battery, thermal state, FPS, network, storage, and recording state
- single-device and multi-device desktop receiver/exporter in `desktop/`
- JSON schemas in `schemas/`
- prototype depth-fusion and trajectory optimization helpers in `freemocap_plugin/`
- FreeMoCap triangulation hook for optional RGB-D refinement from `rgbd_depth_observations.npz`

## Open In Xcode

Open:

```text
MocapCam/MocapCam.xcodeproj
```

Set your development team in Xcode, choose a physical iPhone or iPad, and run the `MocapCam` target. The app requires camera and local-network permissions.

## Build With xtool

`xtool` is supported through the SwiftPM files in this directory:

```text
Package.swift
xtool.yml
```

From WSL:

```bash
cd /mnt/d/freemocap/MocapCam
xtool sdk status
xtool sdk install /path/to/Xcode.xip
xtool dev build
tools/copy_signed_ipa_from_xtool.sh
```

`xtool sdk status` must show an installed Darwin SDK before the app can compile. Installing that SDK requires an Apple-authenticated Xcode `.xip` file.

From Windows PowerShell or `cmd.exe`, the repo root has wrappers that call into WSL:

```powershell
.\build_mocapcam_wsl.ps1
.\build_mocapcam_wsl.ps1 -InstallXtool -InstallSdk
.\build_mocapcam_wsl.ps1 -InstallSdk
.\build_mocapcam_wsl.ps1 -Udid 00008130-001828EC2EDA001C
.\build_mocapcam_wsl.ps1 -Mode Build
.\build_mocapcam_wsl.ps1 -SkipUsbUnbind
```

The default `Ipa` mode builds `MocapCam/xtool/MocapCam.app`, has xtool sign and install it on the connected Apple USB device, copies the install-validated signed IPA to `MocapCam/xtool/MocapCam.ipa`, and verifies that the archive contains an app code signature. `Build` mode writes only `MocapCam/xtool/MocapCam.app`.

IPA mode closes Windows Apple/iTunes USB client processes, force-binds and attaches the Apple USB device to WSL through `usbipd`, then unbinds it after the build so the phone returns to Windows. This can trigger Windows administrator prompts; pass `-SkipUsbUnbind` to leave USB sharing unchanged after signing.

Before signing, the helper resets WSL `usbmuxd`, waits for `idevice_id` to see the phone, and validates pairing. Keep the phone unlocked and accept any Trust prompt.

`-InstallSdk` runs `xtool sdk install` first. The wrapper auto-detects `D:\Xcode_26.5_Apple_silicon.xip` when present, or you can pass `-XcodeXipPath D:\path\to\Xcode.xip`.

`-InstallXtool` downloads the latest official xtool AppImage into WSL under `~/.local/share/xtool` and creates a `~/.local/bin/xtool` wrapper.

## Control Commands

Send newline-delimited JSON to the advertised TCP port:

```json
{"command":"start_preview"}
{"command":"start_recording","session_id":"2026-06-07_iphone_mocap_test"}
{"command":"arm_recording","session_id":"2026-06-07_iphone_mocap_test","start_at_server_time_ns":123456789000}
{"command":"stop_recording"}
{"command":"stop_preview"}
{"command":"set_device_name","device_id":"iphone_01"}
{"command":"set_camera_settings","camera_settings":{"schema_version":1,"camera_selection":"auto_back","resolution":"1920x1080","fps":30,"exposure_mode":"locked","exposure_bias":0.0,"focus_mode":"locked","white_balance_mode":"locked","depth_mode":"fast_lidar"}}
{"command":"ping","request_id":"sync_0001","server_time_send_ns":123456789000}
{"command":"list_local_files","request_id":"files_0001"}
{"command":"download_local_file","request_id":"chunk_0001","file_path":"sessions/mocapcam_test/iphone_01/device_motion.json","offset":0,"length":262144}
```

Frame/status packets sent by the app use the `FMC1` binary framing described in `PROTOCOL.md`.

## Desktop Receiver

```bash
cd MocapCam/desktop
python -m freemocap_capture_server.cli discover
python -m freemocap_capture_server.cli record 192.168.1.50 54321 --session-id mocapcam_test --duration 20 --output recordings
python -m freemocap_capture_server.cli record-multi 192.168.1.50:54321 192.168.1.51:54321 192.168.1.52:54321 192.168.1.53:54321 --session-id mocapcam_test --duration 20 --output recordings
```

The receiver writes a FreeMoCap-style folder with `synchronized_videos/`, `output_data/raw_data/rgbd_frame_manifest.json`, `device_sync_report.json`, and per-device raw RGB-D streams.

`record-multi` warms up clock sync, sends a scheduled start to every device with an estimated device timestamp, prints a compact capture cockpit, requests local file manifests at the end of the take, and downloads small recovery files such as manifests and IMU samples.

The iOS app starts as a monitoring surface with live camera preview, LiDAR overlay, device/session IDs, status, and a settings sheet. Recording is controlled from FreeMoCap.

The FreeMoCap Qt interface also has a `MocapCam` tab for discovery, manual `host:port` entry, multi-device recording, synchronized camera settings, and local file recovery. Completed captures are automatically selected as the active recording.

## RGB-D Reconstruction Hook

FreeMoCap processing now has an optional `RGB-D Depth Fusion` parameter group in the Process Data controls. When `use_depth_fusion` is enabled and `output_data/raw_data/rgbd_depth_observations.npz` exists, the normal triangulated skeleton is refined with depth observations and saved as `<tracker>_rgbd_refined_raw_3d_data.npy` with diagnostics.
