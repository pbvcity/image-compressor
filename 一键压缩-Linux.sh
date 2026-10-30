#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
exec sh "$SCRIPT_DIR/scripts/start_posix.sh" "$@"
