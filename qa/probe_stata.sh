#!/bin/bash
# Thin wrapper: the cross-platform implementation is qa/stata_run.py.
#
#   python qa/stata_run.py --dir path/to/probes
#
# Runs every .do file in the given directory (default /tmp/probes) through
# the licensed Stata and prints each log.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
DIR="${1:-/tmp/probes}"
exec "$PYTHON" "$HERE/stata_run.py" --dir "$DIR"