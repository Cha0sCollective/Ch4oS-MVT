#!/bin/sh
set -eu
exec xvfb-run -a -s '-screen 0 854x480x24 -nolisten tcp' java "$@"
