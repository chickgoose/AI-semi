#!/usr/bin/env bash
set -euo pipefail

aer_die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

aer_require_var() {
  local name="$1"
  [[ -n "${!name:-}" ]] || aer_die "required variable ${name} is empty"
}

aer_abs_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$AER_PROJECT_ROOT" "$path"
  fi
}

aer_init() {
  AER_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  export AER_PROJECT_ROOT
}

aer_record_manifest() {
  local destination="$1"
  {
    printf 'run_id=%s\n' "$AER_RUN_ID"
    printf 'utc_started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'git_commit=%s\n' "$(git -C "$AER_PROJECT_ROOT" rev-parse HEAD)"
    printf 'design=%s\n' "$AER_DESIGN"
    printf 'stage=%s\n' "$AER_STAGE"
    printf 'top=%s\n' "$AER_TOP"
    printf 'rtl_filelist=%s\n' "$AER_RTL_FILELIST"
    printf 'sdc=%s\n' "$AER_SDC"
    printf 'corner=%s\n' "$AER_CORNER"
    printf 'clock_period_ns=%s\n' "$AER_CLOCK_PERIOD_NS"
    printf 'power_activity=%s\n' "${AER_POWER_ACTIVITY:-}"
    printf 'driver=%s\n' "$AER_DRIVER"
  } > "$destination"
}
