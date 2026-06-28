#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXE="${PYTHON_EXE:-python3}"
SPEC_PATH="$PROJECT_ROOT/JingleAllTheDay.spec"
DIST_PATH="$PROJECT_ROOT/dist"
WORK_PATH="$PROJECT_ROOT/build"

if ! command -v "$PYTHON_EXE" >/dev/null 2>&1; then
    echo "Python executable not found: $PYTHON_EXE" >&2
    exit 1
fi

if [[ ! -f "$SPEC_PATH" ]]; then
    echo "Spec file not found: $SPEC_PATH" >&2
    exit 1
fi

mkdir -p "$DIST_PATH" "$WORK_PATH"

"$PYTHON_EXE" -m PyInstaller --noconfirm --clean --distpath "$DIST_PATH" --workpath "$WORK_PATH" "$SPEC_PATH"

BUNDLE_DIR="$DIST_PATH/JingleAllTheDay"
if [[ ! -d "$BUNDLE_DIR" ]]; then
    echo "Expected bundle directory not found: $BUNDLE_DIR" >&2
    exit 1
fi

REV_LOG="$PROJECT_ROOT/rev.log"
if [[ -f "$REV_LOG" ]]; then
    cp "$REV_LOG" "$BUNDLE_DIR"/
    echo "Copied rev.log to $BUNDLE_DIR"
fi

echo "Build complete."
echo "Output: $BUNDLE_DIR"
