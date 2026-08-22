#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$script_dir:$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -S -B -m unittest discover -s "$script_dir" -p 'test_*.py' -v
