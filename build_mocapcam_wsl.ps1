# Builds MocapCam from Windows by running xtool inside WSL.
# Default behavior writes an IPA to MocapCam/xtool/MocapCam.ipa:
#   .\build_mocapcam_wsl.ps1
# Optional:
#   .\build_mocapcam_wsl.ps1 -Mode Build
#   .\build_mocapcam_wsl.ps1 -Udid 00008130-001828EC2EDA001C
#   .\build_mocapcam_wsl.ps1 -InstallXtool
#   .\build_mocapcam_wsl.ps1 -InstallSdk
#   .\build_mocapcam_wsl.ps1 -SkipUsbUnbind
# After the build, Apple USB devices shared with WSL through usbipd are unbound.

[CmdletBinding()]
param(
    [ValidateSet("Ipa", "Build")]
    [string]$Mode = "Ipa",

    [string]$Udid = "",

    [string]$Distro = "",

    [string]$XcodeXipPath = "",

    [switch]$InstallXtool,

    [switch]$InstallSdk,

    [switch]$SkipSdkCheck,

    [switch]$SkipUsbUnbind
)

$ErrorActionPreference = "Stop"

function Quote-Bash {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $wslArguments = @()
    if ($Distro) {
        $wslArguments += @("-d", $Distro)
    }
    $wslArguments += $Arguments

    & wsl.exe @wslArguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-WslOutput {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $wslArguments = @()
    if ($Distro) {
        $wslArguments += @("-d", $Distro)
    }
    $wslArguments += $Arguments

    $output = & wsl.exe @wslArguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
    return ($output -join "`n").Trim()
}

function Get-UsbipdPath {
    $command = Get-Command usbipd -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidatePaths = @()
    if ($env:ProgramFiles) {
        $candidatePaths += (Join-Path $env:ProgramFiles "usbipd-win\usbipd.exe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidatePaths += (Join-Path ${env:ProgramFiles(x86)} "usbipd-win\usbipd.exe")
    }

    foreach ($candidatePath in $candidatePaths) {
        if (Test-Path $candidatePath) {
            return $candidatePath
        }
    }

    return $null
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Argument)

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    return '"' + ($Argument -replace '"', '\"') + '"'
}

function Invoke-LocalProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mocapcam_stdout_{0}.log" -f [System.Guid]::NewGuid().ToString("N"))
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mocapcam_stderr_{0}.log" -f [System.Guid]::NewGuid().ToString("N"))

    try {
        $argumentString = ($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $argumentString `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -Wait `
            -PassThru

        $stdout = @()
        $stderr = @()
        if (Test-Path $stdoutPath) {
            $stdout = @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue)
        }
        if (Test-Path $stderrPath) {
            $stderr = @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue)
        }

        return [PSCustomObject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
            Output = @($stdout) + @($stderr)
        }
    } finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-UsbipdCommand {
    param(
        [Parameter(Mandatory = $true)][string]$UsbipdPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$ElevateOnAccessDenied,
        [switch]$ThrowOnFailure
    )

    $result = Invoke-LocalProcess -FilePath $UsbipdPath -Arguments $Arguments
    $output = $result.Output
    $exitCode = $result.ExitCode
    if ($exitCode -eq 0) {
        if ($output) {
            $output | ForEach-Object { Write-Host $_ }
        }
        return $true
    }

    if ($exitCode -eq 3 -and $ElevateOnAccessDenied) {
        Write-Host "usbipd $($Arguments -join ' ') requires administrator privileges; requesting elevation."

        $escapedUsbipdPath = $UsbipdPath -replace "'", "''"
        $argumentsLiteral = "@(" + (($Arguments | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ", ") + ")"
        $elevatedCommand = @"
`$ErrorActionPreference = 'Continue'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    `$PSNativeCommandUseErrorActionPreference = `$false
}
`$arguments = $argumentsLiteral
& '$escapedUsbipdPath' @arguments
exit `$LASTEXITCODE
"@
        $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($elevatedCommand))

        try {
            $process = Start-Process `
                -FilePath "powershell.exe" `
                -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encodedCommand) `
                -Verb RunAs `
                -WindowStyle Hidden `
                -Wait `
                -PassThru
            if ($process.ExitCode -eq 0) {
                return $true
            }

            $message = "Elevated usbipd $($Arguments -join ' ') failed with exit code $($process.ExitCode)."
            if ($ThrowOnFailure) {
                throw $message
            }
            Write-Warning $message
            return $false
        } catch {
            if ($ThrowOnFailure) {
                throw
            }
            Write-Warning "Elevated usbipd $($Arguments -join ' ') was not completed: $_"
            return $false
        }
    }

    $message = "usbipd $($Arguments -join ' ') failed with exit code $exitCode."
    if ($output) {
        $message += "`n$($output -join "`n")"
    }

    if ($ThrowOnFailure) {
        throw $message
    }

    Write-Warning $message
    return $false
}

function Get-UsbipdState {
    param([Parameter(Mandatory = $true)][string]$UsbipdPath)

    $result = Invoke-LocalProcess -FilePath $UsbipdPath -Arguments @("state")
    if ($result.ExitCode -ne 0) {
        $message = "usbipd state failed with exit code $($result.ExitCode)."
        if ($result.Output) {
            $message += "`n$($result.Output -join "`n")"
        }
        throw $message
    }

    return (($result.Stdout -join "`n") | ConvertFrom-Json)
}

function Get-AppleUsbDevices {
    param(
        [Parameter(Mandatory = $true)][string]$UsbipdPath,
        [string]$DeviceUdid = ""
    )

    $state = Get-UsbipdState -UsbipdPath $UsbipdPath
    $appleDevices = @($state.Devices) | Where-Object {
        $_.InstanceId -match "VID_05AC" -or $_.Description -match "(?i)\b(Apple|iPhone|iPad|iPod)\b"
    }

    $normalizedUdid = $DeviceUdid -replace "[^0-9A-Fa-f]", ""
    if ($normalizedUdid) {
        $appleDevices = @($appleDevices) | Where-Object {
            (($_.InstanceId -replace "[^0-9A-Fa-f]", "") -match [regex]::Escape($normalizedUdid))
        }
    }

    return @($appleDevices)
}

function Stop-AppleUsbWindowsClients {
    $processes = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -like "AppleMobileDevice*" -or
        $_.ProcessName -like "AppleDevices*" -or
        $_.ProcessName -eq "iTunes"
    })

    if ($processes.Count -eq 0) {
        return
    }

    $processNames = ($processes | Select-Object -ExpandProperty ProcessName -Unique) -join ", "
    Write-Host "Closing Windows Apple USB clients before WSL attach: $processNames"

    try {
        $processes | Stop-Process -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not close all Windows Apple USB client processes: $_"
    }

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remaining = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessName -like "AppleMobileDevice*" -or
            $_.ProcessName -like "AppleDevices*" -or
            $_.ProcessName -eq "iTunes"
        })
        if ($remaining.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    $remainingNames = ($remaining | Select-Object -ExpandProperty ProcessName -Unique) -join ", "
    Write-Warning "Windows Apple USB clients are still running and may keep the phone busy: $remainingNames"
}

function Ensure-AppleUsbAttachedToWsl {
    if ($Mode -ne "Ipa") {
        return
    }

    $usbipdPath = Get-UsbipdPath
    if (-not $usbipdPath) {
        throw "Signed IPA generation requires usbipd.exe so xtool can see the iPhone in WSL."
    }

    $appleDevices = @(Get-AppleUsbDevices -UsbipdPath $usbipdPath -DeviceUdid $Udid)
    if ($appleDevices.Count -eq 0) {
        if ($Udid) {
            throw "No connected Apple USB device matched UDID $Udid."
        }
        throw "No connected Apple USB device was found for xtool signing."
    }
    if ($appleDevices.Count -gt 1 -and -not $Udid) {
        $choices = ($appleDevices | ForEach-Object { "$($_.BusId) $($_.Description)" }) -join "; "
        throw "Multiple Apple USB devices were found ($choices). Pass -Udid to choose the signing device."
    }

    $device = $appleDevices[0]
    Write-Host "Using Apple USB device for signing: $($device.BusId) $($device.Description)"
    Stop-AppleUsbWindowsClients

    if (($device.PersistedGuid -or $device.ClientIPAddress -or $device.StubInstanceId) -and -not $device.IsForced) {
        Write-Host "Rebinding Apple USB device with usbipd --force so Windows releases it for WSL."
        Invoke-UsbipdCommand `
            -UsbipdPath $usbipdPath `
            -Arguments @("unbind", "--busid", $device.BusId) `
            -ElevateOnAccessDenied `
            -ThrowOnFailure | Out-Null

        $device = @(Get-AppleUsbDevices -UsbipdPath $usbipdPath -DeviceUdid $Udid | Where-Object { $_.BusId -eq $device.BusId } | Select-Object -First 1)[0]
    }

    if (-not $device.IsForced) {
        Invoke-UsbipdCommand `
            -UsbipdPath $usbipdPath `
            -Arguments @("bind", "--busid", $device.BusId, "--force") `
            -ElevateOnAccessDenied `
            -ThrowOnFailure | Out-Null
    }

    $device = @(Get-AppleUsbDevices -UsbipdPath $usbipdPath -DeviceUdid $Udid | Where-Object { $_.BusId -eq $device.BusId } | Select-Object -First 1)[0]
    if (-not ($device.ClientIPAddress -or $device.StubInstanceId)) {
        Stop-AppleUsbWindowsClients
        $attachArguments = @("attach", "--busid", $device.BusId, "--wsl")
        if ($Distro) {
            $attachArguments += $Distro
        }

        Invoke-UsbipdCommand `
            -UsbipdPath $usbipdPath `
            -Arguments $attachArguments `
            -ElevateOnAccessDenied `
            -ThrowOnFailure | Out-Null
    }
}

function Invoke-UsbipdUnbind {
    param(
        [Parameter(Mandatory = $true)][string]$UsbipdPath,
        [Parameter(Mandatory = $true)][string]$BusId
    )

    Invoke-UsbipdCommand `
        -UsbipdPath $UsbipdPath `
        -Arguments @("unbind", "--busid", $BusId) `
        -ElevateOnAccessDenied | Out-Null
}

function Unbind-AppleUsbFromWsl {
    if ($SkipUsbUnbind) {
        return
    }

    $usbipdPath = Get-UsbipdPath
    if (-not $usbipdPath) {
        Write-Warning "usbipd.exe was not found; skipping Apple USB unbind."
        return
    }

    try {
        $state = Get-UsbipdState -UsbipdPath $usbipdPath
        $appleDevices = @($state.Devices) | Where-Object {
            ($_.InstanceId -match "VID_05AC" -or $_.Description -match "(?i)\b(Apple|iPhone|iPad|iPod)\b") -and
            ($_.PersistedGuid -or $_.ClientIPAddress -or $_.StubInstanceId)
        }

        foreach ($device in $appleDevices) {
            Write-Host "Unbinding Apple USB device from WSL: $($device.BusId) $($device.Description)"
            Invoke-UsbipdUnbind -UsbipdPath $usbipdPath -BusId $device.BusId
        }
    } catch {
        Write-Warning "Could not unbind Apple USB devices from WSL: $_"
    }
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe was not found. Install WSL before building MocapCam from Windows."
}

$repoRoot = $PSScriptRoot
$mocapCamRoot = Join-Path $repoRoot "MocapCam"
if (-not (Test-Path (Join-Path $mocapCamRoot "xtool.yml"))) {
    if ((Split-Path -Leaf $repoRoot) -ieq "MocapCam" -and (Test-Path (Join-Path $repoRoot "xtool.yml"))) {
        $mocapCamRoot = $repoRoot
    } else {
        throw "Could not find MocapCam\xtool.yml from $repoRoot"
    }
}

$mocapCamRootForWslPath = $mocapCamRoot -replace "\\", "/"
$wslMocapCamRoot = Invoke-WslOutput -Arguments @("wslpath", "-a", $mocapCamRootForWslPath)
$quotedRoot = Quote-Bash $wslMocapCamRoot
$quotedUdid = if ($Udid) { Quote-Bash $Udid } else { "" }

if (-not $XcodeXipPath -and (Test-Path "D:\Xcode_26.5_Apple_silicon.xip")) {
    $XcodeXipPath = "D:\Xcode_26.5_Apple_silicon.xip"
}

$quotedXcodeXipPath = ""
if ($XcodeXipPath) {
    if (-not (Test-Path $XcodeXipPath)) {
        throw "Xcode .xip not found: $XcodeXipPath"
    }
    $xcodeXipPathForWslPath = (Resolve-Path $XcodeXipPath).Path -replace "\\", "/"
    $wslXcodeXipPath = Invoke-WslOutput -Arguments @("wslpath", "-a", $xcodeXipPathForWslPath)
    $quotedXcodeXipPath = Quote-Bash $wslXcodeXipPath
}

$preflight = @"
set -euo pipefail
cd $quotedRoot
if [[ -f "`${HOME}/.local/share/swiftly/env.sh" ]]; then
    # shellcheck source=/dev/null
    source "`${HOME}/.local/share/swiftly/env.sh"
fi
export PATH="`${HOME}/.local/share/swiftly/bin:`${HOME}/.local/bin:`${PATH}"
"@

if ($InstallXtool) {
    $xtoolInstallBlock = @'
if ! command -v xtool >/dev/null 2>&1; then
    arch="$(uname -m)"
    case "${arch}" in
        x86_64|aarch64) ;;
        arm64) arch="aarch64" ;;
        *)
            echo "Unsupported xtool AppImage architecture: ${arch}" >&2
            exit 127
            ;;
    esac

    xtool_dir="${HOME}/.local/share/xtool"
    xtool_appimage="${xtool_dir}/xtool-${arch}.AppImage"
    xtool_wrapper="${HOME}/.local/bin/xtool"
    xtool_url="https://github.com/xtool-org/xtool/releases/latest/download/xtool-${arch}.AppImage"

    mkdir -p "${xtool_dir}" "${HOME}/.local/bin"
    echo "Installing xtool from ${xtool_url}"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --output "${xtool_appimage}" "${xtool_url}"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "${xtool_appimage}" "${xtool_url}"
    else
        python3 - "${xtool_url}" "${xtool_appimage}" <<'PY'
import sys
import urllib.request

url, output_path = sys.argv[1:]
urllib.request.urlretrieve(url, output_path)
PY
    fi
    chmod +x "${xtool_appimage}"
    cat > "${xtool_wrapper}" <<EOF
#!/usr/bin/env bash
export APPIMAGE_EXTRACT_AND_RUN=1
exec "${xtool_appimage}" "\$@"
EOF
    chmod +x "${xtool_wrapper}"
fi

'@
    $preflight += "`n$xtoolInstallBlock"
}

$xtoolRequiredBlock = @"
if ! command -v xtool >/dev/null 2>&1; then
    echo "xtool was not found in the selected WSL distro PATH." >&2
    echo "Run this wrapper with -InstallXtool, or install xtool manually." >&2
    exit 127
fi
"@
$preflight += "`n$xtoolRequiredBlock"

if ($Mode -eq "Ipa") {
    $xtoolAuthRequiredBlock = @'
if ! xtool auth status | tee /tmp/mocapcam_xtool_auth_status.log | grep -q '^Logged in\.'; then
    echo "xtool is not logged in, so it cannot sign/provision MocapCam.ipa." >&2
    echo "Recommended login for paid Apple Developer accounts:" >&2
    echo "  xtool auth login --mode key" >&2
    echo "Password login uses Apple private developer-service endpoints and will fail if those endpoints are blocked by your network." >&2
    exit 1
fi
'@
    $preflight += "`n$xtoolAuthRequiredBlock"
} else {
    $preflight += "`nxtool auth status || true"
}

if ($InstallSdk) {
    if (-not $quotedXcodeXipPath) {
        throw "Use -XcodeXipPath <path-to-Xcode.xip> with -InstallSdk, or place the file at D:\Xcode_26.5_Apple_silicon.xip."
    }
    $preflight += "`nxtool sdk install $quotedXcodeXipPath"
}

if (-not $SkipSdkCheck -or $InstallSdk) {
    $preflight += "`nxtool sdk status"
}

switch ($Mode) {
    "Build" {
        $body = @"
xtool dev build
echo
echo "Built app bundle: $wslMocapCamRoot/xtool/MocapCam.app"
"@
    }
    "Ipa" {
        $udidArgument = if ($quotedUdid) { " $quotedUdid" } else { "" }
        $body = @"
rm -f xtool/MocapCam.ipa
xtool dev build
echo "Signing/installing with xtool to produce MocapCam.ipa..."
bash tools/copy_signed_ipa_from_xtool.sh$udidArgument
if [[ ! -s xtool/MocapCam.ipa ]]; then
    echo "Expected IPA was not produced: $wslMocapCamRoot/xtool/MocapCam.ipa" >&2
    exit 1
fi
if ! unzip -l xtool/MocapCam.ipa | grep -Eq 'Payload/[^/]+\.app/_CodeSignature/CodeResources'; then
    echo "Expected signed IPA, but no app code signature was found in $wslMocapCamRoot/xtool/MocapCam.ipa" >&2
    exit 1
fi
ls -lh xtool/MocapCam.ipa
echo
echo "Signed IPA: $wslMocapCamRoot/xtool/MocapCam.ipa"
"@
    }
}

Write-Host "MocapCam WSL path: $wslMocapCamRoot"
Write-Host "Mode: $Mode"
if ($Udid) {
    Write-Host "Target UDID: $Udid"
}
if ($InstallSdk) {
    Write-Host "Installing xtool SDK from: $XcodeXipPath"
}
if ($InstallXtool) {
    Write-Host "Installing xtool into WSL if missing"
}

$tempScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mocapcam_wsl_build_{0}.sh" -f [System.Guid]::NewGuid().ToString("N"))
$tempScriptPathForWslPath = $tempScriptPath -replace "\\", "/"
$scriptText = "$preflight`n$body"
$scriptText = $scriptText -replace "`r`n", "`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false

try {
    Ensure-AppleUsbAttachedToWsl
    [System.IO.File]::WriteAllText($tempScriptPath, $scriptText, $utf8NoBom)
    $wslTempScriptPath = Invoke-WslOutput -Arguments @("wslpath", "-a", $tempScriptPathForWslPath)
    Invoke-Wsl -Arguments @("bash", $wslTempScriptPath)
} finally {
    Remove-Item -LiteralPath $tempScriptPath -Force -ErrorAction SilentlyContinue
    Unbind-AppleUsbFromWsl
}

if ($Mode -eq "Build") {
    Write-Host "Windows app bundle path: $(Join-Path $mocapCamRoot 'xtool\MocapCam.app')"
} elseif ($Mode -eq "Ipa") {
    Write-Host "Windows IPA path: $(Join-Path $mocapCamRoot 'xtool\MocapCam.ipa')"
}
