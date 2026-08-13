#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PNR_TCL="$SCRIPT_DIR/k2_physical_innovus_pnr.tcl"
MMMC_TCL="$SCRIPT_DIR/k2_physical_innovus_mmmc.tcl"
VERIFY="$SCRIPT_DIR/verify_k2_physical_innovus.py"

required=(AER_TOP AER_PNR_NETLIST AER_TECH_LEF AER_CELL_LEF AER_IO_FILE
          AER_SETUP_LIBRARY_FILE AER_HOLD_LIBRARY_FILE AER_SETUP_QRC_TECH
          AER_HOLD_QRC_TECH AER_PNR_OUTPUT_DIR AER_CORE_SITE
          AER_PROCESS_NODE_NM AER_CORE_ASPECT_RATIO AER_CORE_UTILIZATION
          AER_CORE_MARGIN_UM AER_VDD_NET AER_VSS_NET AER_VDD_PIN AER_VSS_PIN
          AER_RING_HORIZONTAL_LAYER AER_RING_VERTICAL_LAYER AER_RING_WIDTH_UM
          AER_RING_SPACING_UM AER_RING_OFFSET_UM AER_PNR_SDC)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { printf 'missing %s\n' "$name" >&2; exit 2; }
done
export "${required[@]}"

for path in "$AER_PNR_NETLIST" "$AER_TECH_LEF" "$AER_CELL_LEF" "$AER_IO_FILE" \
            "$AER_SETUP_LIBRARY_FILE" "$AER_HOLD_LIBRARY_FILE" \
            "$AER_SETUP_QRC_TECH" "$AER_HOLD_QRC_TECH" "$AER_PNR_SDC" \
            "$PNR_TCL" "$MMMC_TCL" "$VERIFY"; do
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'required regular non-symlink file missing: %s\n' "$path" >&2
    exit 2
  }
done

[[ "$AER_SETUP_LIBRARY_FILE" != "$AER_HOLD_LIBRARY_FILE" ]] || {
  printf 'setup and hold Liberty paths must differ\n' >&2; exit 2;
}
[[ "$AER_SETUP_QRC_TECH" != "$AER_HOLD_QRC_TECH" ]] || {
  printf 'setup and hold QRC paths must differ\n' >&2; exit 2;
}
[[ ! -e "$AER_PNR_OUTPUT_DIR" ]] || {
  printf 'output already exists: %s\n' "$AER_PNR_OUTPUT_DIR" >&2; exit 2;
}

INNOVUS_BIN="${AER_INNOVUS_BIN:-innovus}"
command -v "$INNOVUS_BIN" >/dev/null 2>&1 || {
  printf 'Innovus not found: %s\n' "$INNOVUS_BIN" >&2; exit 2;
}

mkdir -p "$AER_PNR_OUTPUT_DIR"
export AER_PNR_MMMC="$MMMC_TCL"
set +e
(cd "$PROJECT_ROOT" && "$INNOVUS_BIN" -no_gui -files "$PNR_TCL") \
  >"$AER_PNR_OUTPUT_DIR/tool.log" 2>&1
tool_status=$?
set -e
if [[ "$tool_status" -ne 0 ]]; then
  printf 'Innovus exited nonzero: %s\n' "$tool_status" >&2
  exit 1
fi

python3 -B "$VERIFY" --run-dir "$AER_PNR_OUTPUT_DIR" --top "$AER_TOP" \
  --write-clean-marker
