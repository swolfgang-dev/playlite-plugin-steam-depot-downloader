#!/usr/bin/env bash
set -euo pipefail
installer_dir=$(cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$installer_dir/install.py" "$@"
