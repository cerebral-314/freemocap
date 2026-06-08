# MocapCam Development Setup

## Build iOS App

```bash
cd /mnt/d/freemocap/MocapCam
xtool sdk status
xtool dev build
tools/copy_signed_ipa_from_xtool.sh
```

Successful signed IPA builds write:

```text
MocapCam/xtool/MocapCam.ipa
```

From Windows, run the WSL wrapper at the repo root:

```powershell
.\build_mocapcam_wsl.ps1
```

Other useful wrapper modes:

```powershell
.\build_mocapcam_wsl.ps1 -Mode Build
.\build_mocapcam_wsl.ps1 -InstallXtool -InstallSdk -Mode Build
.\build_mocapcam_wsl.ps1 -InstallSdk
.\build_mocapcam_wsl.ps1 -Udid 00008130-001828EC2EDA001C
.\build_mocapcam_wsl.bat
.\build_mocapcam_wsl.ps1 -SkipUsbUnbind
```

By default, the wrapper builds `MocapCam/xtool/MocapCam.app`, has xtool sign and install it on the connected Apple USB device, copies the install-validated signed IPA to `MocapCam/xtool/MocapCam.ipa`, and verifies that the archive contains an app code signature.

IPA mode closes Windows Apple/iTunes USB client processes, force-binds and attaches the Apple USB device to WSL through `usbipd`, then unbinds it after the build so the phone returns to Windows. This can trigger Windows administrator prompts; pass `-SkipUsbUnbind` to leave USB sharing unchanged after signing.

Before signing, the helper resets WSL `usbmuxd`, waits for `idevice_id` to see the phone, and validates pairing. Keep the phone unlocked and accept any Trust prompt.

`-InstallSdk` auto-detects `D:\Xcode_26.5_Apple_silicon.xip` when present. To use a different Xcode archive, pass `-XcodeXipPath D:\path\to\Xcode.xip`.

`-InstallXtool` downloads the latest xtool AppImage into WSL and creates a `~/.local/bin/xtool` wrapper.

## Run Desktop Tests

```powershell
$env:PYTHONPATH="D:\freemocap\MocapCam\desktop;D:\freemocap\MocapCam;D:\freemocap"
python -m pytest MocapCam\desktop\tests -q
```

## Receive One Device

```bash
cd MocapCam/desktop
python -m freemocap_capture_server.cli discover
python -m freemocap_capture_server.cli record <device-host> <device-port> --session-id test_take --duration 20
python -m freemocap_capture_server.cli record-multi <host1:port> <host2:port> <host3:port> <host4:port> --session-id test_take --duration 20
```

`record-multi` does a clock-sync warmup, sends a scheduled start, keeps printing the capture cockpit during the take, and requests small local recovery files after stop.

## Use From FreeMoCap

Launch the normal FreeMoCap Qt interface and open the `MocapCam` tab. The tab can discover Bonjour devices or accept manual `host:port` endpoints. After a capture finishes, FreeMoCap marks the new MocapCam recording as the active recording.

For RGB-D reconstruction, open the normal Process Data controls and expand `3d Triangulation Methods` > `RGB-D Depth Fusion`.
