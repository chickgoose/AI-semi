#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_ROOT="${GANGHEE_RESULTS_ROOT:-/tmp/ganghee-a23-results}"
SIMULATORS="${GANGHEE_SIMULATORS:-iverilog verilator}"
SEEDS="${GANGHEE_SEEDS:-1 2 3 4 5}"
TRACE="${GANGHEE_TRACE:-0}"
FILELIST="$PROJECT_ROOT/tests/compat/ganghee/ganghee_a23.f"

mkdir -p "$RESULTS_ROOT"

find_tool() {
  local requested="$1"
  local fallback="$2"
  if [[ -n "$requested" ]]; then
    printf '%s\n' "$requested"
  elif command -v "$fallback" >/dev/null 2>&1; then
    command -v "$fallback"
  else
    printf 'missing tool: %s\n' "$fallback" >&2
    return 1
  fi
}

# name|N|QDEPTH|workload|arrival|background|hot|phase|cycles|set|backpressure
CASES=(
  'u16-p03|16|32|uniform|3|3|50|400|3000|center|always'
  'u16-p05|16|32|uniform|5|3|50|400|3000|center|always'
  'u16-p06|16|32|uniform|6|3|50|400|3000|center|always'
  'u16-p15|16|32|uniform|15|3|50|400|3000|center|always'
  'h16-center|16|64|hotspot|3|3|50|400|3000|center|always'
  'h16-corner|16|64|hotspot|3|3|50|400|3000|corner|always'
  'm16|16|64|moving-hotspot|3|3|50|400|1200|center|always'
  'u64-p03|64|64|uniform|3|2|20|1500|6000|center|always'
  'h64-center|64|64|hotspot|3|2|20|1500|6000|center|always'
  'h64-periphery|64|64|hotspot|3|2|20|1500|6000|periphery|always'
  'm64|64|64|moving-hotspot|3|2|20|1500|4500|center|always'
  'bp16-random|16|32|uniform|5|3|50|400|3000|center|random'
  'bp64-random|64|64|uniform|3|2|20|1500|6000|center|random'
)

if [[ -n "${GANGHEE_CASE_FILTER:-}" ]]; then
  filtered=()
  for spec in "${CASES[@]}"; do
    IFS='|' read -r name _ <<< "$spec"
    if [[ " $GANGHEE_CASE_FILTER " == *" $name "* ]]; then
      filtered+=("$spec")
    fi
  done
  CASES=("${filtered[@]}")
fi

run_binary() {
  local binary="$1"
  local log="$2"
  local seed="$3"
  local name="$4"
  local n="$5"
  local qdepth="$6"
  local workload="$7"
  local arrival="$8"
  local background="$9"
  local hot="${10}"
  local phase="${11}"
  local cycles="${12}"
  local hotspot_set="${13}"
  local backpressure="${14}"
  local wave
  local -a command=("$binary"
    "+SEED=$seed" "+WORKLOAD=$workload" "+ARRIVAL_PCT=$arrival"
    "+BACKGROUND_PCT=$background" "+HOTSPOT_PCT=$hot"
    "+PHASE_LEN=$phase" "+CYCLES=$cycles" "+HOTSPOT_SET=$hotspot_set"
    "+BACKPRESSURE=$backpressure")
  if [[ "$TRACE" == "1" ]]; then
    wave="${log%.log}.vcd"
    command+=("+WAVE=$wave")
  fi
  printf 'GANGHEE_A23_RUN case=%s seed=%s N=%s qdepth=%s\n' \
    "$name" "$seed" "$n" "$qdepth" | tee "$log"
  "${command[@]}" 2>&1 | tee -a "$log"
}

run_iverilog() {
  local iverilog_bin vvp_bin ivl_base spec name n qdepth workload arrival
  local background hot phase cycles hotspot_set backpressure key build_dir binary seed log wrapper
  local -a ivl_args
  declare -A binaries=()
  iverilog_bin="$(find_tool "${GANGHEE_IVERILOG_BIN:-}" iverilog)"
  vvp_bin="$(find_tool "${GANGHEE_VVP_BIN:-}" vvp)"
  ivl_base="${GANGHEE_IVL_BASE:-}"
  for spec in "${CASES[@]}"; do
    IFS='|' read -r name n qdepth workload arrival background hot phase cycles hotspot_set backpressure <<< "$spec"
    key="n${n}-q${qdepth}"
    build_dir="$RESULTS_ROOT/iverilog/build-$key"
    binary="$build_dir/ganghee_a23.vvp"
    if [[ -z "${binaries[$key]:-}" ]]; then
      mkdir -p "$build_dir"
      ivl_args=(-g2012 -Wall -s ganghee_a23_compat_tb
        -P "ganghee_a23_compat_tb.NUM_SOURCES=$n"
        -P "ganghee_a23_compat_tb.QDEPTH=$qdepth"
        -o "$binary" -f "$FILELIST")
      [[ -n "$ivl_base" ]] && ivl_args=(-B "$ivl_base" "${ivl_args[@]}")
      (cd "$PROJECT_ROOT" && "$iverilog_bin" "${ivl_args[@]}") \
        > "$build_dir/compile.log" 2>&1
      binaries[$key]="$binary"
    fi
    mkdir -p "$RESULTS_ROOT/iverilog/$name"
    for seed in $SEEDS; do
      log="$RESULTS_ROOT/iverilog/$name/seed-$seed.log"
      wrapper="$RESULTS_ROOT/iverilog/$name/vvp-seed-$seed.sh"
      if [[ -n "$ivl_base" ]]; then
        printf '#!/usr/bin/env bash\nexec %q -M %q %q "$@"\n' \
          "$vvp_bin" "$ivl_base" "${binaries[$key]}" > "$wrapper"
      else
        printf '#!/usr/bin/env bash\nexec %q %q "$@"\n' \
          "$vvp_bin" "${binaries[$key]}" > "$wrapper"
      fi
      chmod +x "$wrapper"
      run_binary "$wrapper" "$log" "$seed" "$name" "$n" "$qdepth" \
        "$workload" "$arrival" "$background" "$hot" "$phase" "$cycles" \
        "$hotspot_set" "$backpressure"
    done
  done
}

run_verilator() {
  local verilator_bin spec name n qdepth workload arrival background hot phase
  local cycles hotspot_set backpressure key build_dir binary seed log
  local -a trace_args
  declare -A binaries=()
  verilator_bin="$(find_tool "${GANGHEE_VERILATOR_BIN:-}" verilator)"
  trace_args=()
  [[ "$TRACE" == "1" ]] && trace_args+=(--trace)
  for spec in "${CASES[@]}"; do
    IFS='|' read -r name n qdepth workload arrival background hot phase cycles hotspot_set backpressure <<< "$spec"
    key="n${n}-q${qdepth}"
    build_dir="$RESULTS_ROOT/verilator/build-$key"
    binary="$build_dir/obj_dir/Vganghee_a23_compat_tb"
    if [[ -z "${binaries[$key]:-}" ]]; then
      mkdir -p "$build_dir"
      (cd "$PROJECT_ROOT" && "$verilator_bin" --binary --timing --assert \
        -Wall -Wno-fatal --top-module ganghee_a23_compat_tb \
        "-GNUM_SOURCES=$n" "-GQDEPTH=$qdepth" --Mdir "$build_dir/obj_dir" \
        "${trace_args[@]}" -f "$FILELIST") > "$build_dir/compile.log" 2>&1
      binaries[$key]="$binary"
    fi
    mkdir -p "$RESULTS_ROOT/verilator/$name"
    for seed in $SEEDS; do
      log="$RESULTS_ROOT/verilator/$name/seed-$seed.log"
      run_binary "${binaries[$key]}" "$log" "$seed" "$name" "$n" "$qdepth" \
        "$workload" "$arrival" "$background" "$hot" "$phase" "$cycles" \
        "$hotspot_set" "$backpressure"
    done
  done
}

for simulator in $SIMULATORS; do
  case "$simulator" in
    iverilog) run_iverilog ;;
    verilator) run_verilator ;;
    *) printf 'unsupported simulator: %s\n' "$simulator" >&2; exit 2 ;;
  esac
done

pass_files="$(rg -l '^GANGHEE_A23_RESULT PASS ' "$RESULTS_ROOT" -g 'seed-*.log' || true)"
fail_files="$(rg -l '^GANGHEE_A23_(FAIL|RESULT FAIL) ' "$RESULTS_ROOT" -g 'seed-*.log' || true)"
pass_count="$(printf '%s\n' "$pass_files" | sed '/^$/d' | wc -l)"
fail_count="$(printf '%s\n' "$fail_files" | sed '/^$/d' | wc -l)"
printf 'GANGHEE_A23_SUMMARY pass=%s fail_logs=%s results=%s\n' \
  "$pass_count" "$fail_count" "$RESULTS_ROOT"
