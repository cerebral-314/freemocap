# MocapCam Recording Format

The desktop receiver writes a normal FreeMoCap recording folder and stores RGB-D extras under `output_data/raw_data` and `raw_streams`.

```text
<session_id>/
  synchronized_videos/
    <device_id>.mp4
  camera_calibration_data.toml
  raw_streams/
    <device_id>/
      video_annexb.h264
      frame_manifest.json
      depth_uint16_mm/
        000000.bin
  recovered_local_files/
    <device_id>/
      sessions/
        <session_id>/
          <device_id>/
            device_motion.json
            depth_manifest.json
  output_data/
    raw_data/
      rgbd_frame_manifest.json
      device_sync_report.json
      device_calibration_metadata.json
      rgbd_depth_observations.npz
```

`camera_calibration_data.toml` is a placeholder until the normal FreeMoCap ChArUco calibration workflow produces the real calibration file.

Depth chunks are little-endian `uint16` millimeters. A value of `0` means missing or invalid depth.

`device_calibration_metadata.json` stores the last reported device status plus the synchronized camera settings used for the take, including camera selection, resolution, FPS, exposure, focus, white balance, and depth mode.

`rgbd_depth_observations.npz` is optional. When present, it must contain:

```text
depth_points_xyz    # frames x tracked_points x xyz, FreeMoCap world coordinates
depth_valid_mask    # frames x tracked_points, boolean
```

The FreeMoCap processing hook can use that file to save `<tracker>_rgbd_refined_raw_3d_data.npy` and `<tracker>_rgbd_depth_fusion_diagnostics.npy`.

The desktop receiver muxes `<device_id>.mp4` only when `ffmpeg` is available. The raw Annex-B H.264 stream is always preserved for repair or remuxing.
