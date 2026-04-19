#!/usr/bin/env bash
# Install a Collector APK to multiple AVDs in parallel.
#
# Usage:
#   install_collector_to_avds.sh <apk-path> <avd-name> [avd-name ...]
#
# Each AVD is booted headless, the APK is installed via adb install -r,
# then the emulator is shut down. One AVD's failure does not affect others.
set -u

APK="${1:-}"
shift || true
if [[ -z "${APK}" ]] || [[ "$#" -eq 0 ]]; then
  echo "usage: $0 <apk-path> <avd-name> [avd-name ...]" >&2
  exit 2
fi

if [[ ! -f "${APK}" ]]; then
  echo "error: APK not found: ${APK}" >&2
  exit 2
fi

: "${ANDROID_HOME:?ANDROID_HOME must be set}"
export PATH="${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/emulator:${PATH}"

LOG_DIR="/tmp/monkey-install-logs"
mkdir -p "${LOG_DIR}"

install_to_avd() {
  local avd="$1"
  local port="$2"
  local serial="emulator-${port}"
  local log="${LOG_DIR}/${avd}.log"
  {
    echo "[${avd}] starting on port ${port} (serial=${serial})"
    emulator -avd "${avd}" -no-window -no-audio -no-boot-anim \
      -read-only -port "${port}" >"${log}.emu" 2>&1 &
    local emu_pid=$!

    # Wait for device (adb connection)
    if ! timeout 120 adb -s "${serial}" wait-for-device; then
      echo "[${avd}] wait-for-device timed out"
      kill "${emu_pid}" 2>/dev/null || true
      return 1
    fi

    # Wait for boot_completed
    local waited=0
    while true; do
      local out
      out=$(adb -s "${serial}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')
      if [[ "${out}" == "1" ]]; then
        break
      fi
      sleep 2
      waited=$((waited + 2))
      if [[ "${waited}" -ge 180 ]]; then
        echo "[${avd}] boot_completed timeout after ${waited}s"
        adb -s "${serial}" emu kill 2>/dev/null || true
        kill "${emu_pid}" 2>/dev/null || true
        return 1
      fi
    done
    echo "[${avd}] booted after ${waited}s"

    # Install
    if adb -s "${serial}" install -r "${APK}" >"${log}.install" 2>&1; then
      echo "[${avd}] installed OK"
      local rc=0
    else
      echo "[${avd}] install FAILED (see ${log}.install)"
      local rc=1
    fi

    # Verify
    if adb -s "${serial}" shell pm list packages com.monkey.collector \
        | grep -q "^package:com.monkey.collector"; then
      echo "[${avd}] verified: com.monkey.collector present"
    else
      echo "[${avd}] verify FAILED"
      rc=1
    fi

    # Shutdown
    adb -s "${serial}" emu kill 2>/dev/null || true
    wait "${emu_pid}" 2>/dev/null || true
    return "${rc}"
  } 2>&1 | tee -a "${log}"
}

pids=()
i=0
for avd in "$@"; do
  port=$((5554 + 2 * i))
  install_to_avd "${avd}" "${port}" &
  pids+=($!)
  i=$((i + 1))
  # small stagger so concurrent emulator starts don't collide
  sleep 1
done

rc_total=0
for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    rc_total=1
  fi
done

echo ""
echo "== summary =="
echo "logs at ${LOG_DIR}/"
exit "${rc_total}"
