#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s <baseline|improved|mock> [--config FILE] [--filelist FILE]\n' "$0" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
design="$1"
shift
config_request="${AER_CONFIG:-}"
filelist_override=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) [[ $# -ge 2 ]] || usage; config_request="$2"; shift 2 ;;
    --filelist) [[ $# -ge 2 ]] || usage; filelist_override="$2"; shift 2 ;;
    *) usage ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "$config_request")"
source "$config"

case "$design" in
  baseline)
    design_define="AER_DUT_BASELINE"
    design_filelist="${filelist_override:-$AER_BASELINE_FILELIST}"
    ;;
  improved)
    design_define="AER_DUT_IMPROVED"
    design_filelist="${filelist_override:-$AER_IMPROVED_FILELIST}"
    ;;
  mock)
    design_define=""
    design_filelist=""
    ;;
  *) usage ;;
esac

if [[ -n "$design_filelist" ]]; then
  design_filelist="$(aer_abs_path "$design_filelist")"
  [[ -f "$design_filelist" ]] || aer_die "design file list not found: $design_filelist"
fi

SIMULATOR="${AER_SIMULATOR:-}"
OUT_DIR="$(aer_abs_path "${AER_SIM_OUT:-results/sim}/$design")"
mkdir -p "$OUT_DIR"

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

tests=(single simultaneous burst backpressure)
case "$SIMULATOR" in
  xrun)
    xrun_bin="${AER_XRUN_BIN:-xrun}"
    snapshot="aer_tb_${design}"
    command=("$xrun_bin" -64bit -sv -top aer_tb
      -snapshot "$snapshot" -elaborate -xmlibdirname "$OUT_DIR/xcelium.d"
      -defparam "aer_tb.NUM_SOURCES=$AER_NUM_SOURCES"
      -defparam "aer_tb.ADDR_WIDTH=$AER_ADDR_WIDTH"
      -defparam "aer_tb.FIFO_DEPTH=$AER_FIFO_DEPTH"
      -defparam "aer_tb.EVENTS_PER_SOURCE=$AER_EVENTS_PER_SOURCE")
    [[ -n "$design_define" ]] && command+=(-define "$design_define")
    [[ -n "$design_filelist" ]] && command+=(-f "$design_filelist")
    command+=(-f "$AER_PROJECT_ROOT/tb/files.f" -l "$OUT_DIR/elaborate.log")
    (cd "$AER_PROJECT_ROOT" && "${command[@]}")
    for test_name in "${tests[@]}"; do
      run_args=("+TEST=$test_name" "+METRICS=$OUT_DIR/$test_name.csv")
      if [[ "${AER_DUMP_VCD:-0}" == "1" && "$test_name" == "${AER_ACTIVITY_TEST:-backpressure}" ]]; then
        run_args+=("+DUMPFILE=$OUT_DIR/$test_name.vcd")
      fi
      run_command=("$xrun_bin" -64bit -R -snapshot "$snapshot"
        -xmlibdirname "$OUT_DIR/xcelium.d" "${run_args[@]}"
        -l "$OUT_DIR/$test_name.log")
      (cd "$AER_PROJECT_ROOT" && "${run_command[@]}")
    done
    ;;
  iverilog)
    command=(iverilog -g2012 -Wall -s aer_tb
      -P "aer_tb.NUM_SOURCES=$AER_NUM_SOURCES"
      -P "aer_tb.ADDR_WIDTH=$AER_ADDR_WIDTH"
      -P "aer_tb.FIFO_DEPTH=$AER_FIFO_DEPTH"
      -P "aer_tb.EVENTS_PER_SOURCE=$AER_EVENTS_PER_SOURCE"
      -o "$OUT_DIR/aer_tb.vvp")
    [[ -n "$design_define" ]] && command+=("-D$design_define")
    [[ -n "$design_filelist" ]] && command+=(-f "$design_filelist")
    command+=(-f "$AER_PROJECT_ROOT/tb/files.f")
    (cd "$AER_PROJECT_ROOT" && "${command[@]}")
    for test_name in "${tests[@]}"; do
      run_args=("+TEST=$test_name" "+METRICS=$OUT_DIR/$test_name.csv")
      if [[ "${AER_DUMP_VCD:-0}" == "1" && "$test_name" == "${AER_ACTIVITY_TEST:-backpressure}" ]]; then
        run_args+=("+DUMPFILE=$OUT_DIR/$test_name.vcd")
      fi
      vvp "$OUT_DIR/aer_tb.vvp" "${run_args[@]}" |
        tee "$OUT_DIR/$test_name.log"
    done
    ;;
  verilator)
    command=(verilator --binary --timing --assert -Wall -Wno-fatal
      --top-module aer_tb
      "-GNUM_SOURCES=$AER_NUM_SOURCES" "-GADDR_WIDTH=$AER_ADDR_WIDTH"
      "-GFIFO_DEPTH=$AER_FIFO_DEPTH" "-GEVENTS_PER_SOURCE=$AER_EVENTS_PER_SOURCE")
    [[ -n "$design_define" ]] && command+=("-D$design_define")
    [[ -n "$design_filelist" ]] && command+=(-f "$design_filelist")
    command+=(-f "$AER_PROJECT_ROOT/tb/files.f" --Mdir "$OUT_DIR/obj_dir")
    (cd "$AER_PROJECT_ROOT" && "${command[@]}")
    for test_name in "${tests[@]}"; do
      run_args=("+TEST=$test_name" "+METRICS=$OUT_DIR/$test_name.csv")
      if [[ "${AER_DUMP_VCD:-0}" == "1" && "$test_name" == "${AER_ACTIVITY_TEST:-backpressure}" ]]; then
        run_args+=("+DUMPFILE=$OUT_DIR/$test_name.vcd")
      fi
      "$OUT_DIR/obj_dir/Vaer_tb" "${run_args[@]}" |
        tee "$OUT_DIR/$test_name.log"
    done
    ;;
  *) aer_die "unsupported AER_SIMULATOR=$SIMULATOR" ;;
esac

printf 'completed %s regression with %s; results: %s\n' "$design" "$SIMULATOR" "$OUT_DIR"
