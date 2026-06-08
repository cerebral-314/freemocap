# MocapCam Desktop Receiver

This is the FreeMoCap Capture Link desktop receiver. It connects to advertised MocapCam devices, sends control commands, receives `FMC1` packets, and writes a FreeMoCap-compatible recording folder.

```bash
cd MocapCam/desktop
python -m freemocap_capture_server.cli discover
python -m freemocap_capture_server.cli record 192.168.1.50 54321 --session-id four_phone_test --duration 20 --output recordings
python -m freemocap_capture_server.cli record-multi 192.168.1.50:54321 192.168.1.51:54321 192.168.1.52:54321 192.168.1.53:54321 --session-id four_phone_test --duration 20 --output recordings
```

Discovery requires the optional `zeroconf` package. Manual host/port recording uses only the Python standard library.

The receiver writes:

```text
recordings/<session_id>/
  synchronized_videos/
    <device_id>.mp4              # written only if ffmpeg is available
  camera_calibration_data.toml   # placeholder; replace with ChArUco calibration
  raw_streams/
    <device_id>/
      video_annexb.h264
      frame_manifest.json
      depth_uint16_mm/
        000000.bin
  recovered_local_files/
    <device_id>/
  output_data/
    raw_data/
      rgbd_frame_manifest.json
      device_sync_report.json
      device_calibration_metadata.json
```

For multi-device captures, `record-multi` prints a compact cockpit table during warmup and capture with FPS, depth support, clock offset, battery, and quality warnings.

At the end of a multi-device take, the receiver requests each app's local file manifest and downloads small recovery files such as `device_motion.json`, `depth_manifest.json`, and depth chunks below the configured recovery size limit. Large RGB `.mov` files remain on device for manual transfer until a production resumable uploader is added.
