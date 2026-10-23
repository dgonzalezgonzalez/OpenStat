#!/bin/bash
# Thin wrapper: the cross-platform implementation is qa/stata_run.py.
#
#   python qa/stata_run.py path/to/case.do
#   python qa/stata_run.py --dir path/to/probes
#
# Set PYTHON to pick a specific interpreter.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$HERE/stata_run.py" "$@"