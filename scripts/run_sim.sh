#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init

SIMULATOR="${AER_SIMULATOR:-}"
OUT_DIR="${AER_SIM_OUT:-$AER_PROJECT_ROOT/results/sim}"
mkdir -p "$OUT_DIR"

if [[ -z "$SIMULATOR" ]]; then
  if command -v iverilog >/dev/null 2>&1; then
    SIMULATOR="iverilog"
  elif command -v verilator >/dev/null 2>&1; then
    SIMULATOR="verilator"
  else
    aer_die "no simulator found; set AER_SIMULATOR to iverilog or verilator"
  fi
fi

compile_args=()
if [[ "${AER_EXTERNAL_DUT:-0}" == "1" ]]; then
  compile_args+=("-DAER_EXTERNAL_DUT")
  [[ -n "${AER_DUT_FILELIST:-}" ]] || aer_die "AER_DUT_FILELIST is required for external DUT"
fi

case "$SIMULATOR" in
  iverilog)
    command=(iverilog -g2012 -Wall -s aer_tb "${compile_args[@]}" -o "$OUT_DIR/aer_tb.vvp")
    if [[ "${AER_EXTERNAL_DUT:-0}" == "1" ]]; then
      command+=("-f" "$(aer_abs_path "$AER_DUT_FILELIST")")
    fi
    command+=("-f" "$AER_PROJECT_ROOT/tb/files.f")
    (cd "$AER_PROJECT_ROOT" && "${command[@]}")
    for test_name in single simultaneous burst backpressure; do
      vvp "$OUT_DIR/aer_tb.vvp" "+TEST=$test_name" "+METRICS=$OUT_DIR/$test_name.csv"
    done
    ;;
  verilator)
    command=(verilator --binary --timing --assert -Wall -Wno-fatal --top-module aer_tb "${compile_args[@]}")
    if [[ "${AER_EXTERNAL_DUT:-0}" == "1" ]]; then
      command+=("-f" "$(aer_abs_path "$AER_DUT_FILELIST")")
    fi
    command+=("-f" "$AER_PROJECT_ROOT/tb/files.f" --Mdir "$OUT_DIR/obj_dir")
    (cd "$AER_PROJECT_ROOT" && "${command[@]}")
    for test_name in single simultaneous burst backpressure; do
      "$OUT_DIR/obj_dir/Vaer_tb" "+TEST=$test_name" "+METRICS=$OUT_DIR/$test_name.csv"
    done
    ;;
  *) aer_die "unsupported AER_SIMULATOR=$SIMULATOR" ;;
esac
