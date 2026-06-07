# FreeMoCap + Apple LiDAR + Wi‑Fi Capture Plan

## Goal

Modify the open-source [FreeMoCap](https://github.com/freemocap/freemocap) project so that 3 iPhones and 1 iPad with LiDAR can act as synchronized RGB-D motion-capture cameras over Wi‑Fi, replacing the current DroidCam-style workflow and improving mocap fidelity through depth-aware reconstruction.

The core idea is not just to recreate DroidCam. DroidCam-like apps usually make a phone look like a webcam. This project should be **mocap-native**:

```text
iPhone/iPad RGB video
+ LiDAR depth
+ camera intrinsics
+ timestamps
+ frame IDs
+ device pose / gravity / IMU
+ stream health
+ local high-quality recording
→ FreeMoCap-compatible synchronized recording
→ depth-aware higher-fidelity mocap
```

FreeMoCap is a good foundation because it is already designed as a hardware/software-agnostic, low-cost, research-grade motion-capture platform. Its current GUI capture layer uses `skellycam`, while the reconstruction layer consumes synchronized videos and tracked 2D points. That means we can add a new network capture route without rewriting the whole reconstruction stack.

---

## Proposed project name

**FreeMoCap Capture Link**

Main components:

```text
FreeMoCap Capture iOS
  Swift app on iPhone/iPad

FreeMoCap Capture Server
  Desktop receiver / recorder / synchronizer

FreeMoCap RGB-D Plugin
  Importer + depth-fusion reconstruction modifications
```

The most important design decision:

> **Stream for preview/live use, but always record locally on each device too.**

Even with Wi‑Fi 7, the network should be treated as best-effort. For scientific mocap, dropped packets should not destroy the source data.

---

## Target architecture

```text
[ iPhone 1 ]  RGB + LiDAR + timestamps  ┐
[ iPhone 2 ]  RGB + LiDAR + timestamps  ├── Wi‑Fi 7 ──>  [ Desktop Capture Server ] ──> FreeMoCap
[ iPhone 3 ]  RGB + LiDAR + timestamps  ┤
[ iPad    ]  RGB + LiDAR + timestamps  ┘
```

The desktop server should create a normal FreeMoCap-compatible recording folder:

```text
recording_name/
  synchronized_videos/
    iphone_01.mp4
    iphone_02.mp4
    iphone_03.mp4
    ipad_01.mp4

  camera_calibration_data.toml

  output_data/
    raw_data/
      mediapipe_2dData_numCams_numFrames_numTrackedPoints_XY.npy
      lidar_depth_observations.zarr
      rgbd_frame_manifest.json
      device_sync_report.json
      device_calibration_metadata.json
```

FreeMoCap already expects synchronized videos, a calibration file, an output-data folder, and raw-data files. The server should write into those conventions rather than inventing a separate session format.

---

# Phase 1 — Replace DroidCam with our own RGB network camera

Start with RGB only. Do not add LiDAR until the streaming, recording, and synchronization layer is solid.

## iOS app MVP

The iOS app should provide:

1. Device discovery over Bonjour/mDNS.
2. A control channel for start, stop, arm, device name, battery, thermal state, and camera settings.
3. A video channel using H.264 or HEVC encoded frames.
4. Per-frame metadata.

Example frame metadata:

```json
{
  "session_id": "2026-06-07_iphone_mocap_test",
  "device_id": "iphone_01",
  "frame_index": 1821,
  "capture_time_ns": 123456789000,
  "presentation_timestamp": 123.456,
  "width": 1920,
  "height": 1080,
  "focal_length_px": [1200.0, 1200.0],
  "principal_point_px": [960.0, 540.0],
  "orientation": "landscapeRight"
}
```

Recommended Apple frameworks:

- `AVFoundation` for camera capture.
- `VideoToolbox` for hardware video encoding.
- `CoreMedia` for sample buffers and timestamps.
- `Network` for TCP/UDP networking.
- Bonjour/mDNS for discovery.

## Desktop server MVP

The desktop receiver should:

1. Discover devices.
2. Show all live previews.
3. Show FPS, latency, dropped frames, clock offset, battery, and storage.
4. Send a scheduled “start recording at time T” command.
5. Write each stream to disk.
6. Build a timestamp manifest.
7. Export ordinary FreeMoCap-compatible videos.

## Phase 1 success criterion

Four Apple devices stream RGB over Wi‑Fi, the desktop records four videos, FreeMoCap can calibrate and process them, and the result is at least as good as the current DroidCam setup.

---

# Phase 2 — Add proper multi-device time synchronization

Do not synchronize by frame number. Synchronize by timestamps.

Each iOS device should timestamp every RGB/depth frame using the device’s monotonic media clock. The desktop server estimates each device’s clock offset and drift with repeated ping/pong messages:

```text
server_time_send
device_time_receive
device_time_reply
server_time_receive
```

Maintain a rolling estimate:

```text
device_time ≈ server_time + offset + drift * elapsed_time
```

Also add a physical sync event:

```text
start recording
flash LED / display white screen / play clap tone
detect sync event in all videos
refine alignment offline
```

The app should support a command like:

```json
{
  "command": "arm_recording",
  "start_at_server_time_ns": 987654321000
}
```

That way all devices begin recording against a shared future timestamp rather than “whenever the packet arrives.”

## Phase 2 success criterion

The server can produce a `device_sync_report.json` showing clock offset, drift, dropped frames, and final frame alignment error for every device.

---

# Phase 3 — Add LiDAR depth streaming and local RGB-D recording

Once RGB streaming works, add depth.

Apple’s relevant capture APIs include:

- `AVCaptureDepthDataOutput`
- `AVCaptureDataOutputSynchronizer`
- `AVDepthData`
- `ARKit`, optionally, for device pose and scene-depth experiments

The capture app should support two depth modes.

## Mode A — Preview depth stream

Send lower-bandwidth depth over Wi‑Fi for live visualization and sanity checking.

Example depth packet:

```json
{
  "device_id": "iphone_01",
  "frame_index": 1821,
  "rgb_frame_index": 1821,
  "depth_time_ns": 123456789020,
  "depth_width": 320,
  "depth_height": 240,
  "depth_units": "meters",
  "depth_encoding": "uint16_mm_zstd",
  "confidence_encoding": "uint8_optional"
}
```

Even a modest depth stream can be expensive:

```text
320 × 240 × 2 bytes × 30 fps ≈ 4.6 MB/s ≈ 36.9 Mbps per device
```

For four devices:

```text
36.9 Mbps × 4 ≈ 147.6 Mbps
```

That is depth alone, before RGB video, protocol overhead, retransmits, and preview traffic. So raw depth should be compressed and/or recorded locally.

## Mode B — Full local recording

Each device should write full-quality RGB-D data locally, then upload or sync it after the take.

Recommended local files:

```text
iphone_01/
  rgb.mov
  depth.zarr
  confidence.zarr
  frame_manifest.json
  intrinsics.json
  device_motion.json
```

The server can use the live stream for preview but replace or repair the final recording from the local files after capture.

## Phase 3 success criterion

Every RGB frame has either a matched depth frame or a documented missing-depth entry, and the server can visualize RGB + depth alignment.

---

# Phase 4 — Camera calibration strategy

Use FreeMoCap’s existing ChArUco workflow as the ground truth for inter-device calibration.

The current FreeMoCap GUI already supports calibration videos, selecting a ChArUco board, entering square size, and using the initial board position as the origin. Keep that workflow.

## Calibration pipeline

For each device:

1. Record a calibration sequence with the ChArUco board visible to all devices.
2. Estimate RGB camera intrinsics/extrinsics using FreeMoCap’s current calibration system.
3. Store calibration in the existing `camera_calibration_data.toml`.
4. Store Apple-reported intrinsics/depth metadata separately.
5. Estimate or verify RGB-to-depth alignment.
6. Use ARKit/device pose only as an initial guess or diagnostic, not as the final truth.

Why not rely only on ARKit world poses?

Because ARKit poses can drift and can disagree between devices. For mocap, the calibrated multi-camera coordinate system should be the authority.

---

# Phase 5 — Modify FreeMoCap’s reconstruction path

FreeMoCap’s current triangulation path is already clear:

```python
get_triangulated_data(...)
    → load calibration TOML
    → triangulate_3d_data(...)
    → save raw 3D data and reprojection error
```

The relevant call site loads the Anipose calibration TOML, calls `triangulate_3d_data`, and saves raw 3D data plus reprojection errors. The triangulation function currently takes 2D data shaped as cameras × frames × tracked points × XY, triangulates, computes reprojection error, and returns 3D points plus per-camera error data.

Add a new branch:

```python
get_triangulated_data(...)
    if rgbd_depth_available:
        skel3d = triangulate_rgbd_data(...)
    else:
        skel3d = triangulate_3d_data(...)
```

Or better, preserve the existing triangulation as the first stage:

```python
triangulated_3d = triangulate_3d_data(...)
rgbd_refined_3d = refine_3d_with_depth(
    triangulated_3d,
    image_2d_data,
    depth_observations,
    calibration
)
```

That keeps existing FreeMoCap behavior intact and lets depth improve the result without becoming a hard dependency.

---

# Phase 6 — Implement depth-aware fusion

For each frame, camera, and tracked joint:

1. Take the 2D landmark:

   ```text
   u, v
   ```

2. Sample a small patch in the depth map around that point:

   ```text
   median depth in 5×5 or 7×7 patch
   reject invalid / zero / background / edge-depth pixels
   ```

3. Unproject into camera coordinates:

   ```text
   X_cam = (u - cx) / fx * z
   Y_cam = (v - cy) / fy * z
   Z_cam = z
   ```

4. Transform to FreeMoCap world coordinates:

   ```text
   X_world = T_world_camera @ X_cam
   ```

5. Fuse across views.

A practical first fusion rule:

```text
final_joint =
  weighted average(
    normal multi-view triangulated joint,
    valid LiDAR-unprojected joint observations
  )
```

Weights should depend on:

```text
2D landmark confidence
depth validity
depth patch variance
camera reprojection error
viewing angle
distance from camera
recent joint velocity
whether the joint is probably occluded
```

A better second version is a robust optimization:

```text
minimize over X:

Σ cameras  w_2d ρ( reprojection_error(camera_i, X, landmark_i) )
+ λ_depth  Σ cameras  w_d ρ( depth_residual(camera_i, X, depth_i) )
+ λ_bone   bone_length_penalty
+ λ_time   temporal_smoothness_penalty
+ λ_ground foot_ground_contact_penalty
```

This is where the real fidelity gain will come from. LiDAR should not simply overwrite triangulation; it should act as an additional geometric constraint.

---

# Phase 7 — Use FreeMoCap’s outlier rejection aggressively

With your setup, four cameras is a sweet spot.

FreeMoCap v1.8.0 added optional triangulation outlier rejection and explicitly describes it as useful for 4+ camera systems. The feature uses reprojection error to identify bad camera/keypoint views caused by occlusion, ghost skeletons, or bad detections, then rejects or downweights those views during triangulation.

This is directly relevant to your setup:

```text
4 Apple devices
+ LiDAR depth
+ reprojection outlier rejection
= robust against one bad camera view
```

Suggested defaults for Apple RGB-D mode:

```text
minimum_cameras_for_triangulation = 3
use_triangulate_outlier_rejection = true
maximum_cameras_to_drop = 1
target_reprojection_error = tune per resolution
```

FreeMoCap already has these parameters in `AniposeTriangulate3DParametersModel`, so adding RGB-D options alongside them is straightforward.

Add a depth-fusion parameter model:

```python
class DepthFusionParametersModel(BaseModel):
    use_depth_fusion: bool = True
    depth_weight: float = 1.0
    max_depth_joint_distance_m: float = 0.25
    depth_patch_radius_px: int = 3
    min_valid_depth_pixels: int = 5
    reject_depth_edges: bool = True
    use_depth_for_occlusion_reasoning: bool = True
    save_rgbd_diagnostics: bool = True
```

---

# Phase 8 — Add a live capture UI

The desktop app should have a “capture cockpit”:

```text
Device table:
  iphone_01  connected  60 fps RGB  30 fps depth  offset +1.2 ms  battery 82%
  iphone_02  connected  60 fps RGB  30 fps depth  offset -0.4 ms  battery 76%
  iphone_03  connected  60 fps RGB  30 fps depth  offset +0.8 ms  battery 91%
  ipad_01    connected  60 fps RGB  30 fps depth  offset -1.1 ms  battery 68%

Preview grid:
  RGB
  depth
  skeleton overlay
  ChArUco detection
  dropped-frame warning

Capture controls:
  calibrate
  record mocap
  stop
  upload local high-quality data
  process in FreeMoCap
```

FreeMoCap’s existing camera panel already distinguishes motion-capture recording from calibration recording and has auto-process options, so the new network capture UI should map onto those concepts instead of inventing a different workflow.

---

# Recommended network protocol

Do not start with RTSP or WebRTC unless browser compatibility becomes a requirement.

Use a custom protocol:

```text
Discovery:
  Bonjour / mDNS

Control:
  TCP or WebSocket
  JSON messages

Video stream:
  UDP packets carrying H.264/HEVC access units
  or QUIC/WebTransport later

Depth stream:
  UDP or TCP chunks
  zstd/lzfse-compressed uint16 depth frames

Metadata:
  small reliable JSON/protobuf messages

Local recovery:
  after capture, server requests missing frames/files from each device
```

Why custom?

Because mocap needs synchronized timestamps, calibration metadata, depth maps, confidence maps, dropped-frame accounting, and local repair. Webcam protocols are not designed for that.

Suggested staged transport:

```text
v1:
  TCP control + TCP video/depth for simplicity

v2:
  TCP control + UDP media + local file repair

v3:
  QUIC/WebTransport-style transport with multiplexed streams
```

---

# Concrete implementation roadmap

## Milestone 1 — RGB network camera replacement

Deliverable:

```text
iOS app streams RGB to desktop
desktop records 4 synchronized MP4 files
FreeMoCap processes them
```

Tasks:

```text
Swift iOS:
  AVCaptureSession
  rear wide camera
  hardware encode H.264/HEVC
  timestamp every CMSampleBuffer
  send frames to desktop

Desktop:
  device discovery
  preview grid
  record stream to mp4
  create FreeMoCap recording folder
```

## Milestone 2 — FreeMoCap-compatible recording writer

Deliverable:

```text
One button creates:
  synchronized_videos/
  recording metadata
  timestamp manifest
```

Tasks:

```text
Normalize filenames
Create recording folder
Write frame manifest
Add dropped-frame report
Add import button in FreeMoCap or companion app
```

## Milestone 3 — Multi-device sync

Deliverable:

```text
All devices start together and frames align by timestamp
```

Tasks:

```text
clock offset estimation
clock drift estimation
scheduled start
flash/clap sync refinement
sync report
frame reindexing
```

## Milestone 4 — Calibration mode

Deliverable:

```text
Record ChArUco calibration video from all devices
generate / reuse FreeMoCap calibration TOML
```

Tasks:

```text
show ChArUco detection live
verify all cameras see board
store intrinsics
store lens settings
lock exposure/focus/white balance
```

Locking exposure/focus/white balance matters because changing camera parameters mid-take can change tracking quality and calibration assumptions.

## Milestone 5 — LiDAR capture

Deliverable:

```text
RGB + depth + confidence + metadata from each device
```

Tasks:

```text
AVCaptureDepthDataOutput path
AVCaptureDataOutputSynchronizer path
depth/RGB frame association
depth compression
local depth recording
depth preview
depth upload after recording
```

## Milestone 6 — Depth fusion prototype

Deliverable:

```text
FreeMoCap output improves during occlusions and fast limb motion
```

Tasks:

```text
sample depth around 2D landmarks
unproject depth to 3D
transform to calibrated world space
fuse with triangulated 3D
save diagnostics
compare against baseline
```

## Milestone 7 — Robust optimization

Deliverable:

```text
less jitter, fewer impossible limbs, better foot contact
```

Tasks:

```text
robust least-squares solver
bone length constraints
temporal smoothness
joint velocity limits
foot-ground locking
depth outlier rejection
camera-specific confidence weights
```

## Milestone 8 — Production polish

Deliverable:

```text
reliable mocap capture system
```

Tasks:

```text
thermal warnings
storage warnings
battery/power warnings
network quality dashboard
automatic file repair
session resume
crash recovery
versioned file format
test recordings
documentation
```

---

# Other mocap improvements worth adding

## 1. Depth-assisted 2D tracking masks

Use depth to segment the actor from the background before 2D pose detection.

This can reduce:

```text
ghost skeletons
background false positives
limb swaps
multi-person confusion
```

Approach:

```text
estimate capture volume depth range
remove pixels too far behind subject
run pose tracking on masked RGB
save mask diagnostics
```

## 2. Per-camera reliability scoring

For each camera and joint, compute:

```text
2D confidence
reprojection error
depth consistency
recent dropped frames
motion blur estimate
occlusion likelihood
```

Then dynamically downweight bad cameras. This complements FreeMoCap’s existing reprojection outlier rejection.

## 3. Limb-length stabilization

After raw 3D reconstruction, estimate subject-specific limb lengths from high-confidence frames.

Then enforce:

```text
upper arm length constant
forearm length constant
thigh length constant
shin length constant
torso dimensions stable
```

This will reduce noisy “rubber skeleton” behavior.

## 4. Foot-ground contact model

Use ChArUco board origin or calibrated ground plane to detect ground height.

Then add:

```text
no foot below floor
low-velocity foot should stay planted
reduce foot sliding
detect jumps separately
```

## 5. Rolling-shutter and motion-blur diagnostics

Phones are excellent cameras but still susceptible to rolling-shutter artifacts during fast motion. Use IMU/device-motion data and frame timestamps to flag frames likely to have motion distortion.

## 6. Better camera placement recommendations

For a four-device setup:

```text
Camera 1: front-left, chest height
Camera 2: front-right, chest height
Camera 3: rear-left or side-left, higher
Camera 4: rear-right or side-right, higher
```

Avoid:

```text
all cameras at same height
all cameras in front
strong backlighting
large reflective surfaces
black glossy clothing
loose clothing hiding joints
```

## 7. On-device preview skeletons

The iOS app can run lightweight pose detection for live feedback, but the final scientific output should still be processed offline on the desktop using the same tracker/version across all cameras. Apple’s Vision framework is relevant for on-device vision tasks, but using it for live preview and using FreeMoCap’s normal tracker offline avoids mixing model outputs in the final dataset.

## 8. Capture-quality score before recording

Before a take, show:

```text
all devices connected
all devices calibrated
all cameras see subject
all clocks synchronized
all depth streams valid
all cameras have stable exposure
network health acceptable
storage available
```

This will prevent many bad takes.

---

# Recommended first build

The first useful version should not try to solve everything.

Build this first:

```text
1. iOS RGB streaming app
2. desktop receiver
3. timestamped recording
4. FreeMoCap-compatible folder export
5. four-device calibration and recording
6. process with normal FreeMoCap
7. compare against DroidCam
```

Then add:

```text
8. LiDAR local recording
9. depth preview
10. depth fusion after normal triangulation
11. robust body/temporal optimizer
```

That sequence gives working mocap early, preserves FreeMoCap compatibility, and turns LiDAR data into an incremental fidelity upgrade instead of a risky rewrite.

---

# Suggested repository structure

One practical structure:

```text
freemocap-capture-link/
  ios/
    FreeMoCapCapture/
      App/
      Capture/
      Networking/
      Recording/
      Calibration/
      Diagnostics/

  desktop/
    freemocap_capture_server/
      discovery/
      control/
      media_receiver/
      recorder/
      sync/
      freemocap_export/
      diagnostics/

  freemocap_plugin/
    rgbd_importer/
    depth_fusion/
    optimization/
    gui/

  schemas/
    frame_manifest.schema.json
    device_metadata.schema.json
    sync_report.schema.json
    depth_observations.schema.json

  docs/
    architecture.md
    protocol.md
    calibration.md
    recording_format.md
    development_setup.md
```

---

# Minimum viable data schemas

## `device_metadata.json`

```json
{
  "device_id": "iphone_01",
  "device_name": "iPhone 15 Pro",
  "role": "camera",
  "rgb_resolution": [1920, 1080],
  "rgb_fps": 60,
  "depth_resolution": [320, 240],
  "depth_fps": 30,
  "camera_position_hint": "front_left",
  "intrinsics_source": "apple_avfoundation",
  "recording_mode": "rgbd_local_plus_preview_stream"
}
```

## `frame_manifest.json`

```json
{
  "session_id": "2026-06-07_iphone_mocap_test",
  "device_id": "iphone_01",
  "frames": [
    {
      "rgb_frame_index": 0,
      "rgb_time_ns": 1000000000,
      "depth_frame_index": 0,
      "depth_time_ns": 1000001000,
      "rgb_file_offset": null,
      "depth_chunk_id": "000000",
      "dropped": false
    }
  ]
}
```

## `device_sync_report.json`

```json
{
  "session_id": "2026-06-07_iphone_mocap_test",
  "devices": {
    "iphone_01": {
      "estimated_offset_ms": 1.2,
      "estimated_drift_ppm": 0.8,
      "rgb_frames_expected": 3600,
      "rgb_frames_received": 3598,
      "depth_frames_expected": 1800,
      "depth_frames_received": 1799,
      "final_alignment_error_ms": 2.1
    }
  }
}
```

---

# Key engineering risks

## 1. Synchronization error

Bad synchronization can look like bad 3D tracking. Timestamp-first design is mandatory.

## 2. Thermal throttling

Multiple iPhones recording RGB, depth, encoding, networking, and possibly on-device preview skeletons may heat up. The app needs thermal-state monitoring and warnings.

## 3. Depth/RGB alignment

Depth maps are lower resolution and may not align perfectly with RGB landmarks. The system needs calibration checks and patch-based robust depth sampling.

## 4. Overtrusting LiDAR

LiDAR can be wrong at edges, hair, hands, shiny surfaces, loose clothing, and distant/low-reflectance areas. Depth should be a weighted constraint, not an absolute truth.

## 5. Network packet loss

Wi‑Fi 7 is excellent, but the app should still assume packets can be late or missing. Local recording plus post-capture repair is the safety net.

## 6. FreeMoCap compatibility drift

Keep the initial export compatible with FreeMoCap’s existing recording folder structure. Add RGB-D metadata as optional extra files, not as a replacement for the current pipeline.

---

# References

## FreeMoCap code areas to inspect

- [`README.md`](https://github.com/freemocap/freemocap/blob/main/README.md) — project goals, install, license, source setup.
- [`freemocap/core_processes/process_motion_capture_videos/processing_pipeline_functions/triangulation_pipeline_functions.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/core_processes/process_motion_capture_videos/processing_pipeline_functions/triangulation_pipeline_functions.py) — where 3D triangulation is called from the processing pipeline.
- [`freemocap/core_processes/capture_volume_calibration/triangulate_3d_data.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/core_processes/capture_volume_calibration/triangulate_3d_data.py) — triangulation function and reprojection-error output.
- [`freemocap/data_layer/recording_models/post_processing_parameter_models.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/data_layer/recording_models/post_processing_parameter_models.py) — triangulation and post-processing parameter models.
- [`freemocap/data_layer/recording_models/recording_info_model.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/data_layer/recording_models/recording_info_model.py) — expected recording folder paths and output files.
- [`freemocap/gui/qt/widgets/camera_controller_group_box.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/gui/qt/widgets/camera_controller_group_box.py) — current capture/calibration GUI concepts.
- [`freemocap/gui/qt/widgets/release_notes_dialogs/versions/v180_release_notes.py`](https://github.com/freemocap/freemocap/blob/main/freemocap/gui/qt/widgets/release_notes_dialogs/versions/v180_release_notes.py) — outlier-rejection explanation and 4+ camera guidance.

## Apple APIs to inspect

- [AVFoundation](https://developer.apple.com/documentation/avfoundation)
- [AVCaptureDepthDataOutput](https://developer.apple.com/documentation/avfoundation/avcapturedepthdataoutput)
- [AVCaptureDataOutputSynchronizer](https://developer.apple.com/documentation/avfoundation/avcapturedataoutputsynchronizer)
- [AVDepthData](https://developer.apple.com/documentation/avfoundation/avdepthdata)
- [VideoToolbox](https://developer.apple.com/documentation/videotoolbox)
- [CoreMedia](https://developer.apple.com/documentation/coremedia)
- [Network framework](https://developer.apple.com/documentation/network)
- [Vision](https://developer.apple.com/documentation/vision)
- [ARKit](https://developer.apple.com/documentation/arkit)

