#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$script_dir${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -B -m unittest discover -s "$script_dir" -p 'test_*.py' -v
