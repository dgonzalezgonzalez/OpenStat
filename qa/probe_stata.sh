#!/bin/bash
# Run every probe file in a directory through the local Stata 19.5 and print
# each log. Used to capture reference behavior while implementing.
set -u
STATA_ROOT="/Applications/StataNow"
STATA_EXE="$STATA_ROOT/StataSE.app/Contents/MacOS/stata-se"
DIR="${1:-/tmp/probes}"
for f in "$DIR"/*.do; do
  [ -e "$f" ] || continue
  name=$(basename "$f" .do)
  work="/tmp/probe-run-$name"
  rm -rf "$work"; mkdir -p "$work"
  cp "$f" "$work/case.do"
  cat > "$work/runner.do" <<EOF
set more off
set linesize 255
capture log close _all
log using "$work/stata.log", text replace
cd "$work"
capture noisily do "case.do"
local qa_result = _rc
display "__OPENSTAT_RETURN_CODE__=" \`qa_result'
log close _all
exit, clear
EOF
  (cd "$STATA_ROOT" && "$STATA_EXE" -b do "$work/runner.do" >/dev/null 2>&1)
  echo "########## $name ##########"
  tail -n +28 "$work/stata.log" 2>/dev/null | sed 's/^/  /'
  echo "  --- rc: $(grep -o '__OPENSTAT_RETURN_CODE__=[0-9]*' "$work/stata.log" 2>/dev/null | tail -1)"
done
