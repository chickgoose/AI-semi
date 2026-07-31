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

aer_resolve_config() {
  local requested="${1:-}"
  local config_dir
  if [[ -n "$requested" ]]; then
    [[ -f "$requested" ]] || aer_die "config not found: $requested"
    config_dir="$(cd "$(dirname "$requested")" && pwd)"
    printf '%s/%s\n' "$config_dir" "$(basename "$requested")"
  elif [[ -f "$AER_PROJECT_ROOT/scripts/config.local.sh" ]]; then
    printf '%s\n' "$AER_PROJECT_ROOT/scripts/config.local.sh"
  else
    printf '%s\n' "$AER_PROJECT_ROOT/scripts/config.example.sh"
  fi
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
    printf 'num_sources=%s\n' "$AER_NUM_SOURCES"
    printf 'addr_width=%s\n' "$AER_ADDR_WIDTH"
    printf 'fifo_depth=%s\n' "$AER_FIFO_DEPTH"
    printf 'library_file=%s\n' "${AER_LIBRARY_FILE:-}"
    printf 'power_activity=%s\n' "${AER_POWER_ACTIVITY:-}"
    printf 'driver=%s\n' "$AER_DRIVER"
  } > "$destination"
}
