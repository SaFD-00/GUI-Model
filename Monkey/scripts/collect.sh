#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Monkey Data Collection ==="

# Default config
CONFIG="${1:-configs/collection/default.yaml}"
APPS_CONFIG="${2:-configs/collection/apps.yaml}"

echo "Config: $CONFIG"
echo "Apps: $APPS_CONFIG"

# Run batch collection
python -m collection.cli batch \
    --config "$CONFIG" \
    --apps-config "$APPS_CONFIG"

echo ""
echo "=== Running Annotation Pipeline ==="

python -m collection.cli annotate \
    --config "$CONFIG"

echo ""
echo "=== Done ==="
echo "Results in: data/processed/"
