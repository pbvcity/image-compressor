#!/bin/sh
# Shared POSIX launcher; also called by the Linux entry point.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
export PYTHONUTF8=1
status=2
found=0
for candidate in "$SCRIPT_DIR/.venv/bin/python" python3 python; do
    if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        found=1
        "$candidate" "$SCRIPT_DIR/scripts/run_default.py"
        status=$?
        break
    fi
done
if [ "$found" -eq 0 ]; then
    printf '\nPython 3.10+ is required. Install Python, then run this file again.\n'
fi
if [ -t 0 ]; then
    printf '\nPress Enter to close...'
    read -r answer
fi
exit "$status"
