# MocapCam Capture Link Protocol

## Discovery

The app publishes one Bonjour service:

```text
type: _mocapcam._tcp
name: <device name>
```

The service port accepts newline-delimited JSON control messages from the desktop receiver.

## Incoming Control

Commands are UTF-8 JSON objects terminated by `\n`.

```json
{
  "command": "arm_recording",
  "session_id": "2026-06-07_iphone_mocap_test",
  "start_at_server_time_ns": 987654321000
}
```

Supported commands:

- `start_preview`
- `stop_preview`
- `start_depth_preview` (compatibility; depth is automatic when supported)
- `stop_depth_preview` (compatibility; depth remains automatic when supported)
- `start_recording`
- `arm_recording`
- `stop_recording`
- `set_device_name`
- `set_camera_settings`
- `ping`
- `list_local_files`
- `download_local_file`

`start_recording`, `arm_recording`, and `set_camera_settings` can include synchronized camera settings:

```json
{
  "command": "set_camera_settings",
  "camera_settings": {
    "schema_version": 1,
    "camera_selection": "auto_back",
    "resolution": "1920x1080",
    "fps": 30,
    "exposure_mode": "locked",
    "exposure_bias": 0.0,
    "focus_mode": "locked",
    "white_balance_mode": "locked",
    "depth_mode": "auto_lidar"
  }
}
```

Older `lock_exposure`, `lock_focus`, and `lock_white_balance` fields are still accepted and are mapped onto the settings object. `camera_selection` supports `auto_back`, `lidar`, `triple`, `dual_wide`, `dual`, `wide`, `ultra_wide`, and `telephoto`. `depth_mode` supports `off`, `auto_lidar`, `fast_lidar`, and `quality_lidar`.

Local recovery uses a manifest request followed by chunked file downloads:

```json
{"command":"list_local_files","request_id":"files_0001"}
{"command":"download_local_file","request_id":"chunk_0001","file_path":"sessions/take_01/iphone_01/device_motion.json","offset":0,"length":262144}
```

## Outgoing Packets

MocapCam sends binary packets to every connected desktop client.

```text
0..3    magic bytes: "FMC1"
4       protocol version: 1
5       packet type
6..7    flags, big-endian uint16
8..11   JSON metadata byte length, big-endian uint32
12..15  payload byte length, big-endian uint32
16..N   UTF-8 JSON metadata
N..M    optional binary payload
```

Packet types:

```text
1 device_status
2 video_frame
3 recording_event
4 error
5 depth_frame
6 clock_sync
7 local_file_manifest
8 local_file_chunk
9 capture_quality
```

Video payloads are H.264 access units in AVCC byte-stream form. Keyframes include base64-encoded H.264 parameter sets in the packet metadata so the desktop receiver can initialize a decoder.

Depth payloads are `uint16_mm_little_endian` raster buffers. A zero depth sample means missing or invalid depth.

Local file chunk payloads are raw bytes from files under the app's `FreeMoCapCapture` document root. The desktop receiver writes them under `recovered_local_files/<device_id>/` and records recovery status in each device manifest.

Clock sync is initiated by the desktop with:

```json
{"command":"ping","request_id":"...","server_time_send_ns":123}
```

The device replies with a `clock_sync` packet containing receive/reply device timestamps. The desktop estimates device clock offset from the lowest-latency samples, then sends scheduled recordings with both:

```json
{
  "command": "arm_recording",
  "start_at_server_time_ns": 10000000000,
  "start_at_device_time_ns": 10001200000
}
```
