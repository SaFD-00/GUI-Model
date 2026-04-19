#!/usr/bin/env bash
# Install a Collector APK to a single AVD.
#
# Usage:
#   install_collector_to_avds.sh <apk-path> <avd-name>
#
# The AVD is booted headless, the APK is installed via adb install -r,
# then the emulator is shut down.
set -u

APK="${1:-}"
AVD="${2:-}"
if [[ -z "${APK}" ]] || [[ -z "${AVD}" ]]; then
  echo "usage: $0 <apk-path> <avd-name>" >&2
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

PORT=5554
SERIAL="emulator-${PORT}"
LOG="${LOG_DIR}/${AVD}.log"

{
  echo "[${AVD}] starting on port ${PORT} (serial=${SERIAL})"
  emulator -avd "${AVD}" -no-window -no-audio -no-boot-anim \
    -read-only -port "${PORT}" >"${LOG}.emu" 2>&1 &
  EMU_PID=$!

  if ! timeout 120 adb -s "${SERIAL}" wait-for-device; then
    echo "[${AVD}] wait-for-device timed out"
    kill "${EMU_PID}" 2>/dev/null || true
    exit 1
  fi

  WAITED=0
  while true; do
    OUT=$(adb -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')
    if [[ "${OUT}" == "1" ]]; then
      break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    if [[ "${WAITED}" -ge 180 ]]; then
      echo "[${AVD}] boot_completed timeout after ${WAITED}s"
      adb -s "${SERIAL}" emu kill 2>/dev/null || true
      kill "${EMU_PID}" 2>/dev/null || true
      exit 1
    fi
  done
  echo "[${AVD}] booted after ${WAITED}s"

  RC=0
  if adb -s "${SERIAL}" install -r "${APK}" >"${LOG}.install" 2>&1; then
    echo "[${AVD}] installed OK"
  else
    echo "[${AVD}] install FAILED (see ${LOG}.install)"
    RC=1
  fi

  if adb -s "${SERIAL}" shell pm list packages com.monkey.collector \
      | grep -q "^package:com.monkey.collector"; then
    echo "[${AVD}] verified: com.monkey.collector present"
  else
    echo "[${AVD}] verify FAILED"
    RC=1
  fi

  adb -s "${SERIAL}" emu kill 2>/dev/null || true
  wait "${EMU_PID}" 2>/dev/null || true

  echo ""
  echo "== summary =="
  echo "logs at ${LOG_DIR}/"
  exit "${RC}"
} 2>&1 | tee -a "${LOG}"
