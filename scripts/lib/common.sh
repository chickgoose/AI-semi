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
  local config_sha256="N/A"
  local config_base_sha256="N/A"
  local sdc_sha256="N/A"
  local library_sha256="N/A"
  [[ -f "$AER_CONFIG_FILE" ]] && config_sha256="$(sha256sum "$AER_CONFIG_FILE" | awk '{print $1}')"
  [[ -f "$AER_PROJECT_ROOT/scripts/config.example.sh" ]] && config_base_sha256="$(sha256sum "$AER_PROJECT_ROOT/scripts/config.example.sh" | awk '{print $1}')"
  [[ -f "$AER_SDC" ]] && sdc_sha256="$(sha256sum "$AER_SDC" | awk '{print $1}')"
  [[ -f "${AER_LIBRARY_FILE:-}" ]] && library_sha256="$(sha256sum "$AER_LIBRARY_FILE" | awk '{print $1}')"
  {
    printf 'run_id=%s\n' "$AER_RUN_ID"
    printf 'utc_started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'git_commit=%s\n' "$(git -C "$AER_PROJECT_ROOT" rev-parse HEAD)"
    printf 'design=%s\n' "$AER_DESIGN"
    printf 'stage=%s\n' "$AER_STAGE"
    printf 'top=%s\n' "$AER_TOP"
    printf 'rtl_filelist=%s\n' "$AER_RTL_FILELIST"
    printf 'config_file=%s\n' "$AER_CONFIG_FILE"
    printf 'config_sha256=%s\n' "$config_sha256"
    printf 'config_base_sha256=%s\n' "$config_base_sha256"
    printf 'sdc=%s\n' "$AER_SDC"
    printf 'sdc_sha256=%s\n' "$sdc_sha256"
    printf 'corner=%s\n' "$AER_CORNER"
    printf 'clock_period_ns=%s\n' "$AER_CLOCK_PERIOD_NS"
    printf 'clock_port=%s\n' "$AER_CLOCK_PORT"
    printf 'reset_port=%s\n' "$AER_RESET_PORT"
    printf 'input_delay_ns=%s\n' "$AER_INPUT_DELAY_NS"
    printf 'output_delay_ns=%s\n' "$AER_OUTPUT_DELAY_NS"
    printf 'clock_uncertainty_ns=%s\n' "$AER_CLOCK_UNCERTAINTY_NS"
    printf 'load_pf=%s\n' "$AER_LOAD_PF"
    printf 'driver_cell=%s\n' "$AER_DRIVER_CELL"
    printf 'num_sources=%s\n' "$AER_NUM_SOURCES"
    printf 'addr_width=%s\n' "$AER_ADDR_WIDTH"
    printf 'fifo_depth=%s\n' "$AER_FIFO_DEPTH"
    printf 'events_per_source=%s\n' "$AER_EVENTS_PER_SOURCE"
    printf 'library_file=%s\n' "${AER_LIBRARY_FILE:-}"
    printf 'library_sha256=%s\n' "$library_sha256"
    printf 'power_mode=%s\n' "$AER_POWER_MODE"
    printf 'power_activity=%s\n' "${AER_POWER_ACTIVITY:-}"
    printf 'driver=%s\n' "$AER_DRIVER"
  } > "$destination"
}
