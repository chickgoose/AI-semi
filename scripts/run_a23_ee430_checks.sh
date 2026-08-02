#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "${AER_CONFIG:-}")"
source "$config"

SIMULATOR="${AER_SIMULATOR:-}"
OUT_ROOT="$(aer_abs_path "${AER_SIM_OUT:-results/sim}/a23-ee430-checks")"
FILELIST="$AER_PROJECT_ROOT/tb/filelists/a23_ee430.f"

if [[ -z "$SIMULATOR" ]]; then
  if command -v "${AER_XRUN_BIN:-xrun}" >/dev/null 2>&1; then
    SIMULATOR="xrun"
  elif command -v iverilog >/dev/null 2>&1; then
    SIMULATOR="iverilog"
  elif command -v verilator >/dev/null 2>&1; then
    SIMULATOR="verilator"
  else
    aer_die "no simulator found; set AER_SIMULATOR to xrun, iverilog, or verilator"
  fi
fi

tests=(stream contention)
source_counts=(1 3 4)

for source_count in "${source_counts[@]}"; do
  for test_name in "${tests[@]}"; do
    top="a23_ee430_${test_name}_tb"
    test_file="$AER_PROJECT_ROOT/tests/a23/${top}.sv"
    out_dir="$OUT_ROOT/n${source_count}/${test_name}"
    mkdir -p "$out_dir"

    case "$SIMULATOR" in
      xrun)
        snapshot="${top}_n${source_count}"
        xrun_bin="${AER_XRUN_BIN:-xrun}"
        (
          cd "$AER_PROJECT_ROOT"
          "$xrun_bin" -64bit -sv -timescale 1ns/1ps -top "$top" \
            -snapshot "$snapshot" -elaborate \
            -xmlibdirname "$out_dir/xcelium.d" \
            -defparam "${top}.NUM_SOURCES=$source_count" \
            -f "$FILELIST" "$test_file" -l "$out_dir/elaborate.log"
          "$xrun_bin" -64bit -R -snapshot "$snapshot" \
            -xmlibdirname "$out_dir/xcelium.d" -l "$out_dir/run.log"
        )
        ;;
      iverilog)
        (
          cd "$AER_PROJECT_ROOT"
          iverilog -g2012 -Wall -s "$top" \
            -P "${top}.NUM_SOURCES=$source_count" \
            -o "$out_dir/test.vvp" -f "$FILELIST" "$test_file"
        )
        vvp "$out_dir/test.vvp" | tee "$out_dir/run.log"
        ;;
      verilator)
        (
          cd "$AER_PROJECT_ROOT"
          verilator --binary --timing --assert -Wall -Wno-fatal \
            --top-module "$top" "-GNUM_SOURCES=$source_count" \
            -f "$FILELIST" "$test_file" --Mdir "$out_dir/obj_dir"
        )
        "$out_dir/obj_dir/V${top}" | tee "$out_dir/run.log"
        ;;
      *) aer_die "unsupported AER_SIMULATOR=$SIMULATOR" ;;
    esac
  done
done

printf 'completed A23 EE430 stream/contention checks with %s; results: %s\n' \
  "$SIMULATOR" "$OUT_ROOT"
