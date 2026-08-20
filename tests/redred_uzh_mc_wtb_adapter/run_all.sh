#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

required=(
  REDRED_UZH_POSE_JOIN_PACKAGE
  REDRED_RUN_UZH_ADAPTER_OFFICIAL
  REDRED_UZH_JOINED_ROOT
  REDRED_UZH_JOIN_SPEC
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "missing required full-gate environment variable: $name" >&2
    exit 2
  fi
done
if [[ "$REDRED_RUN_UZH_ADAPTER_OFFICIAL" != "1" ]]; then
  echo "REDRED_RUN_UZH_ADAPTER_OFFICIAL must be 1 for the full gate" >&2
  exit 2
fi

PYTHONDONTWRITEBYTECODE=1 bash "$script_dir/run_native.sh"
PYTHONDONTWRITEBYTECODE=1 bash "$script_dir/run_independent.sh"
