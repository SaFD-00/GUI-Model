#!/usr/bin/env bash
set -euo pipefail

echo "=== AVD Setup for Monkey-Collector ==="

# Create AVD if not exists
AVD_NAME="monkey_collector"
API_LEVEL=34

if ! avdmanager list avd | grep -q "$AVD_NAME"; then
    echo "Creating AVD: $AVD_NAME (API $API_LEVEL)"
    sdkmanager "system-images;android-$API_LEVEL;google_apis;x86_64"
    echo "no" | avdmanager create avd \
        -n "$AVD_NAME" \
        -k "system-images;android-$API_LEVEL;google_apis;x86_64" \
        --device "pixel_6"
fi

echo "Starting emulator..."
emulator -avd "$AVD_NAME" -no-window -no-audio -gpu swiftshader_indirect &
EMULATOR_PID=$!

# Wait for boot
echo "Waiting for device..."
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
echo "Device ready!"

echo "AVD PID: $EMULATOR_PID"
