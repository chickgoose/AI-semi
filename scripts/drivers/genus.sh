#!/usr/bin/env bash
set -euo pipefail

required=(AER_PROJECT_ROOT AER_TOP AER_RTL_FILELIST AER_SDC AER_OUTPUT_DIR
          AER_LIBRARY_FILE AER_CLOCK_PERIOD_NS)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { printf 'missing %s\n' "$name" >&2; exit 2; }
done
[[ -f "$AER_LIBRARY_FILE" ]] || { printf 'library not found: %s\n' "$AER_LIBRARY_FILE" >&2; exit 2; }
[[ -f "$AER_RTL_FILELIST" ]] || { printf 'file list not found: %s\n' "$AER_RTL_FILELIST" >&2; exit 2; }
[[ -f "$AER_SDC" ]] || { printf 'SDC not found: %s\n' "$AER_SDC" >&2; exit 2; }

if [[ "${AER_POWER_MODE:-}" == "genus_vectorless" && -n "${AER_GENUS_ACTIVITY_TCL:-}" ]]; then
  printf 'vectorless mode must not configure AER_GENUS_ACTIVITY_TCL\n' >&2
  exit 2
fi

if [[ -n "${AER_GENUS_ACTIVITY_TCL:-}" ]]; then
  if [[ "$AER_GENUS_ACTIVITY_TCL" != /* ]]; then
    AER_GENUS_ACTIVITY_TCL="$AER_PROJECT_ROOT/$AER_GENUS_ACTIVITY_TCL"
    export AER_GENUS_ACTIVITY_TCL
  fi
  [[ -f "$AER_GENUS_ACTIVITY_TCL" ]] || {
    printf 'activity Tcl not found: %s\n' "$AER_GENUS_ACTIVITY_TCL" >&2
    exit 2
  }
fi

genus_bin="${AER_GENUS_BIN:-genus}"
command -v "$genus_bin" >/dev/null 2>&1 || { printf 'Genus not found: %s\n' "$genus_bin" >&2; exit 2; }

(cd "$AER_PROJECT_ROOT" && "$genus_bin" -batch -files scripts/drivers/genus_synth.tcl)
"$AER_PROJECT_ROOT/scripts/drivers/extract_genus_metrics.sh" \
  "$AER_OUTPUT_DIR" "$AER_CLOCK_PERIOD_NS"
