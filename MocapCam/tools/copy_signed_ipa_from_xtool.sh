#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_IPA="${PROJECT_ROOT}/xtool/MocapCam.ipa"
TEMP_IPA="${OUTPUT_IPA}.tmp"
LOG_PATH="/tmp/mocapcam_xtool_install.log"
UDID="${1:-}"
MAX_XTOOL_ATTEMPTS="${MAX_XTOOL_ATTEMPTS:-3}"
XTOOL_RETRY_DELAY_SECONDS="${XTOOL_RETRY_DELAY_SECONDS:-8}"
PHONE_CONFIRMATION_WAIT_SECONDS="${PHONE_CONFIRMATION_WAIT_SECONDS:-20}"
PHONE_CONFIRMED=0

rm -f "${OUTPUT_IPA}" "${TEMP_IPA}" "${LOG_PATH}"

cd "${PROJECT_ROOT}"
install_args=(install --usb)
if [[ -n "${UDID}" ]]; then
    install_args+=(--udid "${UDID}")
fi

wait_for_usbmux_device() {
    local expected_udid="${1:-}"
    local seen_udids=""

    usbmuxd -X >/dev/null 2>&1 || true

    for _ in $(seq 1 120); do
        seen_udids="$(idevice_id -l 2>/dev/null || true)"
        if [[ -n "${expected_udid}" ]]; then
            if grep -Fxq "${expected_udid}" <<<"${seen_udids}"; then
                return 0
            fi
        elif [[ -n "${seen_udids}" ]]; then
            return 0
        fi
        sleep 0.5
    done

    echo "Timed out waiting for the iPhone to appear through WSL usbmuxd." >&2
    echo "Attached idevice_id devices:" >&2
    idevice_id -l >&2 2>/dev/null || true
    echo "Raw WSL USB devices:" >&2
    lsusb >&2 2>/dev/null || true
    return 1
}

validate_pairing() {
    local expected_udid="${1:-}"
    local pair_args=()
    local attempt=0
    if [[ -n "${expected_udid}" ]]; then
        pair_args=(-u "${expected_udid}")
    fi

    for attempt in $(seq 1 180); do
        if idevicepair "${pair_args[@]}" validate >/dev/null 2>&1; then
            return 0
        fi

        if [[ "${attempt}" == "1" ]]; then
            echo "Pairing with the iPhone in WSL. Unlock the phone and accept the Trust prompt if one appears."
        fi

        if (( attempt % 5 == 1 )); then
            idevicepair "${pair_args[@]}" pair >/dev/null 2>&1 || true
        fi
        sleep 1
    done

    echo "Timed out waiting for iPhone pairing/trust approval in WSL." >&2
    idevicepair "${pair_args[@]}" validate >&2 2>/dev/null || true
    return 1
}

confirm_phone_ready() {
    local attempt="$1"

    if [[ "${MOCAPCAM_SKIP_PHONE_PROMPT:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "${PHONE_CONFIRMED}" == "1" ]]; then
        return 0
    fi

    echo
    echo "Unlock the iPhone and tap Allow/Trust if prompted before xtool provisions the app."
    if [[ "${attempt}" != "1" ]]; then
        echo "This is retry ${attempt}; recheck the phone in case USB access was requested again."
    fi

    if [[ -t 0 ]]; then
        read -r -p "Press Enter here after the phone is allowed/unlocked: " _ || true
    else
        echo "No interactive input is available; waiting ${PHONE_CONFIRMATION_WAIT_SECONDS}s for phone approval."
        sleep "${PHONE_CONFIRMATION_WAIT_SECONDS}"
    fi
    PHONE_CONFIRMED=1
}

wait_for_lockdown_ready() {
    local expected_udid="${1:-}"
    local device_args=()
    local attempt=0

    if ! command -v ideviceinfo >/dev/null 2>&1; then
        return 0
    fi

    if [[ -n "${expected_udid}" ]]; then
        device_args=(-u "${expected_udid}")
    fi

    for attempt in $(seq 1 180); do
        if ideviceinfo "${device_args[@]}" -k DeviceName >/dev/null 2>&1; then
            return 0
        fi

        if [[ "${attempt}" == "1" ]]; then
            echo "Waiting for iPhone services to become available through WSL..."
        fi
        sleep 1
    done

    echo "Timed out waiting for iPhone services through WSL. Unlock the phone and tap Allow/Trust, then rerun." >&2
    return 1
}

has_code_signature() {
    unzip -l "$1" | grep -Eq 'Payload/[^/]+\.app/_CodeSignature/CodeResources'
}

has_embedded_profile() {
    unzip -l "$1" | grep -Eq 'Payload/[^/]+\.app/embedded\.mobileprovision'
}

validate_ipa_signature_hashes() {
    python3 - "$1" <<'PY'
import hashlib
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ipa_path = Path(sys.argv[1])
tmp_dir = Path(tempfile.mkdtemp(prefix="mocapcam_ipa_verify_"))

try:
    with zipfile.ZipFile(ipa_path) as archive:
        archive.extractall(tmp_dir)

    payload_dir = tmp_dir / "Payload"
    app_dirs = list(payload_dir.glob("*.app"))
    if len(app_dirs) != 1:
        raise SystemExit("IPA must contain exactly one .app in Payload")

    app_dir = app_dirs[0]
    info_plist = app_dir / "Info.plist"
    code_resources = app_dir / "_CodeSignature" / "CodeResources"
    executable_candidates = [p for p in app_dir.iterdir() if p.is_file() and p.name not in {"Info.plist", "embedded.mobileprovision"}]
    executable = next((p for p in executable_candidates if p.read_bytes()[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}), None)
    if executable is None:
        raise SystemExit("Could not find Mach-O executable in app bundle")

    data = executable.read_bytes()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != 0xfeedfacf:
        raise SystemExit("Only little-endian arm64 Mach-O executables are supported by this verifier")

    _, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data, 0)
    offset = 32
    signature = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmd == 0x1D:
            signature = struct.unpack_from("<II", data, offset + 8)
        offset += cmdsize
    if signature is None:
        raise SystemExit("Mach-O executable has no LC_CODE_SIGNATURE")

    sig_offset, sig_size = signature
    superblob = data[sig_offset:sig_offset + sig_size]
    magic, _, count = struct.unpack_from(">III", superblob, 0)
    if magic != 0xFADE0CC0:
        raise SystemExit("Mach-O code signature is not a superblob")

    blobs = {}
    code_directories = []
    for index in range(count):
        blob_type, relative_offset = struct.unpack_from(">II", superblob, 12 + index * 8)
        blob_magic, blob_length = struct.unpack_from(">II", superblob, relative_offset)
        blob = superblob[relative_offset:relative_offset + blob_length]
        blobs[blob_type] = blob
        if blob_magic == 0xFADE0C02:
            code_directories.append((blob_type, blob))

    if not code_directories:
        raise SystemExit("Mach-O code signature has no CodeDirectory")

    special_payloads = {
        1: info_plist.read_bytes(),
        2: blobs.get(2, b""),
        3: code_resources.read_bytes(),
        5: blobs.get(5, b""),
        7: blobs.get(7, b""),
    }

    for blob_type, code_directory in code_directories:
        _, _, _, _, hash_offset, _, special_count, code_count, code_limit = struct.unpack_from(">IIIIIIIII", code_directory, 0)
        hash_size, hash_type, _, page_size_exp = struct.unpack_from(">BBBB", code_directory, 36)
        algorithm = {1: "sha1", 2: "sha256"}.get(hash_type)
        if algorithm is None:
            continue

        def digest(payload):
            return hashlib.new(algorithm, payload).digest()[:hash_size]

        page_size = 1 << page_size_exp
        for slot in range(code_count):
            expected = code_directory[hash_offset + slot * hash_size:hash_offset + (slot + 1) * hash_size]
            actual = digest(data[slot * page_size:min((slot + 1) * page_size, code_limit)])
            if expected != actual:
                raise SystemExit(f"CodeDirectory {blob_type} code page hash mismatch at slot {slot}")

        for slot, payload in special_payloads.items():
            if slot > special_count:
                continue
            expected = code_directory[hash_offset - slot * hash_size:hash_offset - (slot - 1) * hash_size]
            actual = digest(payload)
            if expected != actual:
                raise SystemExit(f"CodeDirectory {blob_type} special slot {slot} hash mismatch")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)
PY
}

install_reached_device() {
    grep -q $'\\[Connecting\\].*100%' "${LOG_PATH}" 2>/dev/null
}

is_retryable_xtool_failure() {
    [[ -f "${LOG_PATH}" ]] || return 1
    grep -Eiq \
        '(deadlineExceeded|Transport threw an error|HTTPClientError|devices_createInstance|timed out|timeout|temporar|network|connection reset|LockdownClient\.Error\.muxError|Failed to connect|Waiting for device to be connected|HTTP[^[:alnum:]]*5[0-9][0-9]|status[^0-9]*5[0-9][0-9])' \
        "${LOG_PATH}"
}

is_apple_developer_services_timeout() {
    [[ -f "${LOG_PATH}" ]] || return 1
    grep -Eiq \
        '(api\.appstoreconnect\.apple\.com|devices_createInstance|devices_getCollection|DeveloperAPI|App Store Connect|HTTPClientError\.deadlineExceeded)' \
        "${LOG_PATH}"
}

print_retryable_failure_hint() {
    if is_apple_developer_services_timeout; then
        cat >&2 <<'EOF'
xtool reached the iPhone, but Apple's Developer Services/App Store Connect API timed out while provisioning.
This is not an iPhone Allow/Trust prompt or a WSL USB failure.

If this repeats, test the xtool account/API session in WSL:
  xtool ds teams list
  xtool ds devices list

If those also end with HTTPClientError.deadlineExceeded, refresh xtool's Apple login:
  xtool auth logout
  xtool auth login

Also check developer.apple.com/account and appstoreconnect.apple.com in a browser for any pending agreement/account prompts.
EOF
    fi
}

run_xtool_install_attempt() {
    local attempt="$1"
    local max_attempts="$2"
    local copied=0
    local xtool_done=0
    local xtool_exit=0
    local timed_out=0
    local xtool_pid=""
    local ipa_path=""
    local size_before=""
    local size_after=""

    rm -f "${TEMP_IPA}" "${LOG_PATH}"
    find "${HOME}/.cache/xtool" -maxdepth 5 -type f -name "*.ipa" -delete 2>/dev/null || true

    if [[ "${attempt}" != "1" ]]; then
        echo "Resetting WSL usbmuxd before retry ${attempt}/${max_attempts}..."
    else
        echo "Resetting WSL usbmuxd and waiting for the iPhone..."
    fi
    wait_for_usbmux_device "${UDID}" || return 75
    validate_pairing "${UDID}" || return 75
    confirm_phone_ready "${attempt}"
    wait_for_lockdown_ready "${UDID}" || return 75

    echo "Waiting for xtool to sign/package/install MocapCam.app (attempt ${attempt}/${max_attempts})..."
    printf "yes\n" | xtool "${install_args[@]}" xtool/MocapCam.app >"${LOG_PATH}" 2>&1 &
    xtool_pid=$!

    for _ in $(seq 1 900); do
        if ! kill -0 "${xtool_pid}" 2>/dev/null; then
            xtool_done=1
        fi

        ipa_path="$(find "${HOME}/.cache/xtool" -maxdepth 5 -type f -name "app.ipa" -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2- || true)"
        if [[ -n "${ipa_path}" && -s "${ipa_path}" ]]; then
            size_before="$(stat -c %s "${ipa_path}")"
            sleep 0.2
            size_after="$(stat -c %s "${ipa_path}")"
            if [[ "${size_before}" == "${size_after}" ]]; then
                if cp "${ipa_path}" "${TEMP_IPA}"; then
                    if has_code_signature "${TEMP_IPA}" && has_embedded_profile "${TEMP_IPA}" && validate_ipa_signature_hashes "${TEMP_IPA}"; then
                        if [[ "${xtool_done}" == "1" ]] || install_reached_device; then
                            if mv "${TEMP_IPA}" "${OUTPUT_IPA}"; then
                                copied=1
                                break
                            fi
                        fi
                    fi
                fi
                rm -f "${TEMP_IPA}"
            fi
        fi

        if [[ "${xtool_done}" == "1" ]]; then
            break
        fi
        sleep 0.2
    done

    if [[ "${copied}" != "1" && "${xtool_done}" != "1" ]]; then
        timed_out=1
        echo "Timed out waiting for xtool to produce a valid signed IPA; stopping xtool." >&2
        if kill -0 "${xtool_pid}" 2>/dev/null; then
            kill "${xtool_pid}" 2>/dev/null || true
        fi
    fi

    if [[ "${copied}" == "1" && "${xtool_done}" != "1" ]]; then
        # xtool sometimes hangs after the device connection reaches 100% even though
        # the signed IPA has already been packaged in its cache.
        echo "xtool reached device connection completion but did not exit; stopping the stale install process."
        sleep 1
        if kill -0 "${xtool_pid}" 2>/dev/null; then
            kill "${xtool_pid}" 2>/dev/null || true
        fi
    fi

    wait "${xtool_pid}" 2>/dev/null || xtool_exit=$?

    cat "${LOG_PATH}" || true

    if [[ "${copied}" != "1" ]]; then
        if [[ "${xtool_exit}" != "0" ]]; then
            echo "xtool failed while signing/installing MocapCam.app" >&2
        else
            echo "Failed to copy a signed IPA from xtool cache" >&2
        fi
        if [[ "${timed_out}" == "1" ]] || is_retryable_xtool_failure; then
            return 75
        fi
        return 1
    fi

    echo "Copied signed IPA from xtool cache after device connection reached 100%."
    return 0
}

attempt=1
while (( attempt <= MAX_XTOOL_ATTEMPTS )); do
    result=0
    run_xtool_install_attempt "${attempt}" "${MAX_XTOOL_ATTEMPTS}" || result=$?

    if [[ "${result}" == "0" ]]; then
        ls -lh "${OUTPUT_IPA}"
        exit 0
    fi

    if [[ "${result}" == "75" && "${attempt}" -lt "${MAX_XTOOL_ATTEMPTS}" ]]; then
        print_retryable_failure_hint
        echo "xtool hit a retryable provisioning/device error; retrying in ${XTOOL_RETRY_DELAY_SECONDS}s..."
        sleep "${XTOOL_RETRY_DELAY_SECONDS}"
        attempt=$((attempt + 1))
        continue
    fi

    exit "${result}"
done

exit 1
