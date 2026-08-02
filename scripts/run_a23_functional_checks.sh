#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "${AER_CONFIG:-}")"
source "$config"

SIMULATOR="${AER_SIMULATOR:-}"
OUT_ROOT="$(aer_abs_path "${AER_SIM_OUT:-results/sim}/a23-functional")"
FILELIST="$AER_PROJECT_ROOT/tb/filelists/a23_ee430.f"
TEST_FILE="$AER_PROJECT_ROOT/tests/a1/a23_functional_tb.sv"
TOP=a23_functional_tb
SOURCE_COUNTS=(1 3 4)
SEEDS=(17 23001 48879)

if [[ -z "$SIMULATOR" ]]; then
  if command -v iverilog >/dev/null 2>&1; then
    SIMULATOR=iverilog
  elif command -v verilator >/dev/null 2>&1; then
    SIMULATOR=verilator
  else
    aer_die "no simulator found; set AER_SIMULATOR to iverilog or verilator"
  fi
fi

for source_count in "${SOURCE_COUNTS[@]}"; do
  out_dir="$OUT_ROOT/$SIMULATOR/n$source_count"
  mkdir -p "$out_dir"
  case "$SIMULATOR" in
    iverilog)
      (
        cd "$AER_PROJECT_ROOT"
        iverilog -g2012 -Wall -s "$TOP" \
          -P "$TOP.NUM_SOURCES=$source_count" \
          -o "$out_dir/test.vvp" -f "$FILELIST" "$TEST_FILE"
      )
      for seed in "${SEEDS[@]}"; do
        vvp "$out_dir/test.vvp" "+SEED=$seed" | tee "$out_dir/seed-$seed.log"
      done
      ;;
    verilator)
      (
        cd "$AER_PROJECT_ROOT"
        verilator --binary --timing --assert -Wall -Wno-fatal \
          --top-module "$TOP" "-GNUM_SOURCES=$source_count" \
          -f "$FILELIST" "$TEST_FILE" --Mdir "$out_dir/obj_dir"
      )
      for seed in "${SEEDS[@]}"; do
        "$out_dir/obj_dir/V$TOP" "+SEED=$seed" | tee "$out_dir/seed-$seed.log"
      done
      ;;
    *) aer_die "unsupported AER_SIMULATOR=$SIMULATOR" ;;
  esac
done

printf 'completed A23 functional checks with %s; results: %s\n' \
  "$SIMULATOR" "$OUT_ROOT"
