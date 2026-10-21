#!/bin/bash
# Run a do-file in the locally installed, licensed Stata 19.5 (batch) and
# print the resulting log. Used only for differential QA.
set -u
STATA_ROOT="/Applications/StataNow"
STATA_EXE="$STATA_ROOT/StataSE.app/Contents/MacOS/stata-se"
DOFILE="$1"
WORKDIR="${2:-/tmp/openstat-stata-$$}"
mkdir -p "$WORKDIR"
cp "$DOFILE" "$WORKDIR/case.do"
rm -f "$WORKDIR/stata.log"
cat > "$WORKDIR/runner.do" <<EOF
set more off
set linesize 255
capture log close _all
log using "$WORKDIR/stata.log", text replace
cd "$WORKDIR"
capture noisily do "case.do"
local qa_result = _rc
display "__OPENSTAT_RETURN_CODE__=" \`qa_result'
log close _all
exit, clear
EOF
cd "$STATA_ROOT" || exit 1
"$STATA_EXE" -b do "$WORKDIR/runner.do" >/dev/null 2>&1
cat "$WORKDIR/stata.log" 2>/dev/null
echo "___WORKDIR___=$WORKDIR"
