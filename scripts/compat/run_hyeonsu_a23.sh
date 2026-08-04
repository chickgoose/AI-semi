#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_ROOT="$PROJECT_ROOT/tests/compat/hyeonsu"
RESULTS_ROOT="${HYEONSU_RESULTS_ROOT:-/tmp/hyeonsu-a23-results}"
SIMULATOR="${HYEONSU_SIMULATOR:-verilator}"
SOURCE_COUNTS="${HYEONSU_SOURCE_COUNTS:-4 64}"
RUN_NATIVE="${HYEONSU_RUN_NATIVE:-1}"
RUN_LONG_STALL="${HYEONSU_RUN_LONG_STALL:-1}"
RUN_ARBITER="${HYEONSU_RUN_ARBITER:-1}"
RUN_SCHEDULER_SAFE="${HYEONSU_RUN_SCHEDULER_SAFE:-1}"

mkdir -p "$RESULTS_ROOT"
STATUS_TSV="$RESULTS_ROOT/status.tsv"
printf 'suite\tsimulator\tsources\tcompile_status\trun_status\tlog\n' > "$STATUS_TSV"

AER_SOURCES=(
  "$TEST_ROOT/aer_pkg_compat.sv"
  "$PROJECT_ROOT/rtl/baseline/aer_rx.sv"
  "$PROJECT_ROOT/rtl/experiments/a23_ee430/a23_ee430_arbiter.sv"
  "$PROJECT_ROOT/rtl/experiments/a23_ee430/a23_ee430_tx.sv"
  "$PROJECT_ROOT/rtl/experiments/a23_ee430/a23_ee430_core.sv"
  "$PROJECT_ROOT/rtl/experiments/a23_ee430/a23_ee430_dut.sv"
  "$TEST_ROOT/aer_top_a23_wrapper.sv"
)
AER_MONITOR_SOURCES=(
  "$TEST_ROOT/a23_compat_monitors.sv"
  "$TEST_ROOT/a23_compat_bind.sv"
)
ARB_SOURCES=(
  "$TEST_ROOT/aer_pkg_compat.sv"
  "$PROJECT_ROOT/rtl/experiments/a23_ee430/a23_ee430_arbiter.sv"
  "$TEST_ROOT/dual_level_a23_adapter.sv"
  "$TEST_ROOT/dual_level_arbiter_tb.sv"
  "$TEST_ROOT/a23_compat_monitors.sv"
  "$TEST_ROOT/a23_compat_bind.sv"
)

record_status() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$SIMULATOR" "$2" "$3" "$4" "$5" >> "$STATUS_TSV"
}

make_long_stall_tb() {
  local destination="$1"
  awk '
    /^[[:space:]]+run_single\(\);/ {next}
    /^[[:space:]]+run_simultaneous\(\);/ {next}
    /^[[:space:]]+run_burst\(\);/ {next}
    /^[[:space:]]+run_backpressure\(\);/ {next}
    /^[[:space:]]+run_starvation_probe\(/ {next}
    /^[[:space:]]+run_all_but_one_saturated\(\);/ {next}
    /^[[:space:]]+run_reset_mid_contention\(\);/ {next}
    /^[[:space:]]+\/\/ run_long_stall_backpressure\(\);/ {
      sub(/\/\/ /, "")
      print
      next
    }
    {print}
  ' "$TEST_ROOT/aer_tb.sv" > "$destination"
}

make_scheduler_safe_tb() {
  local source="$1" destination="$2"
  awk '
    /^  task automatic drive_source\(/ {
      print "  task automatic drive_source(int idx, int n_events, int gap_cycles);"
      print "    for (int k = 0; k < n_events; k++) begin"
      print "      @(negedge clk);"
      print "      in_addr[idx]  = ADDR_WIDTH\047((idx << 8) | k);"
      print "      in_valid[idx] = 1\047b1;"
      print "      do @(posedge clk); while (!in_ready[idx]);"
      print "      for (int gap = 0; gap < gap_cycles; gap++) begin"
      print "        @(negedge clk);"
      print "        in_valid[idx] = 1\047b0;"
      print "        @(posedge clk);"
      print "      end"
      print "    end"
      print "    @(negedge clk);"
      print "    in_valid[idx] = 1\047b0;"
      print "  endtask"
      skip_drive = 1
      next
    }
    skip_drive {
      if (/^  endtask/) skip_drive = 0
      next
    }
    /^  task automatic backpressure_pattern\(/ {
      print
      print "    forever begin"
      print "      repeat (ready_cycles) begin"
      print "        @(negedge clk);"
      print "        out_ready = 1\047b1;"
      print "        @(posedge clk);"
      print "      end"
      print "      repeat (stall_cycles) begin"
      print "        @(negedge clk);"
      print "        out_ready = 1\047b0;"
      print "        @(posedge clk);"
      print "      end"
      print "    end"
      print "  endtask"
      skip_backpressure = 1
      next
    }
    skip_backpressure {
      if (/^  endtask/) skip_backpressure = 0
      next
    }
    /^  task automatic run_backpressure/ { in_run_backpressure = 1 }
    /^  task automatic run_long_stall_backpressure/ { in_long_stall = 1 }
    (in_run_backpressure || in_long_stall) && /^[[:space:]]+out_ready = 1\047b[01];/ {
      print "    @(negedge clk);"
      print
      next
    }
    { print }
    /^  endtask/ {
      in_run_backpressure = 0
      in_long_stall = 0
    }
  ' "$source" > "$destination"
}

check_aer_log() {
  local mode="$1" log="$2"
  grep -q 'ALL TESTS PASSED' "$log" || return 1
  ! grep -q 'TESTS FAILED' "$log" || return 1
  ! grep -Eq '^\*E,|^%Error|^Error:|\$error|Assertion failed|FATAL' "$log" || return 1
  grep -q 'HYEONSU_AER_MONITOR errors=0' "$log" || return 1
  if [[ "$mode" == active* ]]; then
    for workload in single simultaneous burst backpressure starvation_probe \
      all_but_one_saturated reset_mid_contention_recovery; do
      grep -q "$workload" "$log" || return 1
    done
  else
    grep -q 'long_stall_backpressure' "$log" || return 1
  fi
}

check_arb_log() {
  local log="$1"
  grep -q 'ALL TESTS PASSED' "$log" || return 1
  ! grep -q 'TESTS FAILED' "$log" || return 1
  ! grep -Eq '^\*E,|^%Error|^Error:|\$error|Assertion failed|FATAL' "$log" || return 1
  grep -q 'HYEONSU_ARBITER_MONITOR errors=0' "$log" || return 1
}

find_verilator() {
  if [[ -n "${HYEONSU_VERILATOR:-}" ]]; then
    printf '%s\n' "$HYEONSU_VERILATOR"
  elif command -v verilator >/dev/null 2>&1; then
    command -v verilator
  elif [[ -x /tmp/hyeonsu-tools/verilator/usr/bin/verilator ]]; then
    printf '%s\n' /tmp/hyeonsu-tools/verilator/usr/bin/verilator
  else
    printf 'Verilator not found; set HYEONSU_VERILATOR\n' >&2
    return 1
  fi
}

verilator_env() {
  if [[ -n "${HYEONSU_VERILATOR_ROOT:-}" ]]; then
    printf '%s\n' "$HYEONSU_VERILATOR_ROOT"
  elif [[ -d /tmp/hyeonsu-tools/verilator/usr/share/verilator ]]; then
    printf '%s\n' /tmp/hyeonsu-tools/verilator/usr/share/verilator
  else
    printf '\n'
  fi
}

run_verilator_aer() {
  local mode="$1" sources="$2" tb_file="$3" out_dir compile_log run_log binary
  local verilator_bin verilator_root compile_status run_status
  verilator_bin="$(find_verilator)"
  verilator_root="$(verilator_env)"
  out_dir="$RESULTS_ROOT/verilator/aer/n$sources/$mode"
  compile_log="$out_dir/compile.log"
  run_log="$out_dir/run.log"
  mkdir -p "$out_dir"
  local -a command=("$verilator_bin" --binary --timing --assert --trace
    -Wall -Wno-fatal --top-module aer_tb
    -DHYEONSU_BIND_AER "-DHYEONSU_NUM_SOURCES=$sources"
    --Mdir "$out_dir/obj_dir")
  [[ "$mode" == long_stall* ]] && command+=(-DHYEONSU_LONG_STALL)
  command+=("${AER_SOURCES[@]}" "$tb_file" "${AER_MONITOR_SOURCES[@]}")
  set +e
  if [[ -n "$verilator_root" ]]; then
    VERILATOR_ROOT="$verilator_root" PATH="$(dirname "$verilator_bin"):$PATH" \
      "${command[@]}" > "$compile_log" 2>&1
  else
    "${command[@]}" > "$compile_log" 2>&1
  fi
  compile_status=$?
  set -e
  if [[ "$compile_status" -ne 0 ]]; then
    record_status "aer_$mode" "$sources" "FAIL($compile_status)" NOT_RUN "$run_log"
    return 0
  fi
  binary="$out_dir/obj_dir/Vaer_tb"
  set +e
  if [[ "$mode" == long_stall* ]]; then
    "$binary" "+HYEONSU_WAVE=$out_dir/long_stall.vcd" > "$run_log" 2>&1
  else
    "$binary" > "$run_log" 2>&1
  fi
  run_status=$?
  set -e
  if [[ "$run_status" -eq 0 ]] && check_aer_log "$mode" "$run_log"; then
    record_status "aer_$mode" "$sources" PASS PASS "$run_log"
  else
    record_status "aer_$mode" "$sources" PASS "FAIL($run_status)" "$run_log"
  fi
}

run_verilator_arbiter() {
  local out_dir compile_log run_log binary verilator_bin verilator_root compile_status run_status
  verilator_bin="$(find_verilator)"
  verilator_root="$(verilator_env)"
  out_dir="$RESULTS_ROOT/verilator/arbiter/n256"
  compile_log="$out_dir/compile.log"
  run_log="$out_dir/run.log"
  mkdir -p "$out_dir"
  set +e
  if [[ -n "$verilator_root" ]]; then
    VERILATOR_ROOT="$verilator_root" PATH="$(dirname "$verilator_bin"):$PATH" \
      "$verilator_bin" --binary --timing --assert -Wall -Wno-fatal \
      --top-module dual_level_arbiter_tb -DHYEONSU_BIND_ARBITER \
      --Mdir "$out_dir/obj_dir" \
      "${ARB_SOURCES[@]}" > "$compile_log" 2>&1
  else
    "$verilator_bin" --binary --timing --assert -Wall -Wno-fatal \
      --top-module dual_level_arbiter_tb -DHYEONSU_BIND_ARBITER \
      --Mdir "$out_dir/obj_dir" \
      "${ARB_SOURCES[@]}" > "$compile_log" 2>&1
  fi
  compile_status=$?
  set -e
  if [[ "$compile_status" -ne 0 ]]; then
    record_status arbiter 256 "FAIL($compile_status)" NOT_RUN "$run_log"
    return 0
  fi
  binary="$out_dir/obj_dir/Vdual_level_arbiter_tb"
  set +e
  "$binary" > "$run_log" 2>&1
  run_status=$?
  set -e
  if [[ "$run_status" -eq 0 ]] && check_arb_log "$run_log"; then
    record_status arbiter 256 PASS PASS "$run_log"
  else
    record_status arbiter 256 PASS "FAIL($run_status)" "$run_log"
  fi
}

find_xrun() {
  if [[ -n "${HYEONSU_XRUN:-}" ]]; then
    printf '%s\n' "$HYEONSU_XRUN"
  elif command -v xrun >/dev/null 2>&1; then
    command -v xrun
  else
    printf 'xrun not found; set HYEONSU_XRUN\n' >&2
    return 1
  fi
}

run_xrun_aer() {
  local mode="$1" sources="$2" tb_file="$3" out_dir compile_log run_log snapshot
  local xrun_bin compile_status run_status
  xrun_bin="$(find_xrun)"
  out_dir="$RESULTS_ROOT/xrun/aer/n$sources/$mode"
  compile_log="$out_dir/compile.log"
  run_log="$out_dir/run.log"
  snapshot="hyeonsu_aer_${mode}_n${sources}"
  mkdir -p "$out_dir"
  local -a command=("$xrun_bin" -64bit -sv -timescale 1ns/1ps -top aer_tb
    -snapshot "$snapshot" -elaborate -xmlibdirname "$out_dir/xcelium.d"
    -define HYEONSU_BIND_AER -define "HYEONSU_NUM_SOURCES=$sources")
  [[ "$mode" == long_stall* ]] && command+=(-define HYEONSU_LONG_STALL)
  command+=("${AER_SOURCES[@]}" "$tb_file" "${AER_MONITOR_SOURCES[@]}"
    -l "$compile_log")
  set +e
  (cd "$out_dir" && "${command[@]}")
  compile_status=$?
  set -e
  if [[ "$compile_status" -ne 0 ]]; then
    record_status "aer_$mode" "$sources" "FAIL($compile_status)" NOT_RUN "$run_log"
    return 0
  fi
  set +e
  if [[ "$mode" == long_stall* ]]; then
    (cd "$out_dir" && "$xrun_bin" -64bit -R -snapshot "$snapshot" \
      -xmlibdirname "$out_dir/xcelium.d" \
      "+HYEONSU_WAVE=$out_dir/long_stall.vcd" -l "$run_log")
  else
    (cd "$out_dir" && "$xrun_bin" -64bit -R -snapshot "$snapshot" \
      -xmlibdirname "$out_dir/xcelium.d" -l "$run_log")
  fi
  run_status=$?
  set -e
  if [[ "$run_status" -eq 0 ]] && check_aer_log "$mode" "$run_log"; then
    record_status "aer_$mode" "$sources" PASS PASS "$run_log"
  else
    record_status "aer_$mode" "$sources" PASS "FAIL($run_status)" "$run_log"
  fi
}

run_xrun_arbiter() {
  local xrun_bin out_dir compile_log run_log snapshot compile_status run_status
  xrun_bin="$(find_xrun)"
  out_dir="$RESULTS_ROOT/xrun/arbiter/n256"
  compile_log="$out_dir/compile.log"
  run_log="$out_dir/run.log"
  snapshot=hyeonsu_a23_arbiter_n256
  mkdir -p "$out_dir"
  set +e
  (cd "$out_dir" && "$xrun_bin" -64bit -sv -timescale 1ns/1ps \
    -top dual_level_arbiter_tb -define HYEONSU_BIND_ARBITER \
    -snapshot "$snapshot" -elaborate -xmlibdirname "$out_dir/xcelium.d" \
    "${ARB_SOURCES[@]}" -l "$compile_log")
  compile_status=$?
  set -e
  if [[ "$compile_status" -ne 0 ]]; then
    record_status arbiter 256 "FAIL($compile_status)" NOT_RUN "$run_log"
    return 0
  fi
  set +e
  (cd "$out_dir" && "$xrun_bin" -64bit -R -snapshot "$snapshot" \
    -xmlibdirname "$out_dir/xcelium.d" -l "$run_log")
  run_status=$?
  set -e
  if [[ "$run_status" -eq 0 ]] && check_arb_log "$run_log"; then
    record_status arbiter 256 PASS PASS "$run_log"
  else
    record_status arbiter 256 PASS "FAIL($run_status)" "$run_log"
  fi
}

long_tb="$RESULTS_ROOT/generated/aer_tb_long_stall.sv"
safe_tb="$RESULTS_ROOT/generated/aer_tb_scheduler_safe.sv"
safe_long_tb="$RESULTS_ROOT/generated/aer_tb_long_stall_scheduler_safe.sv"
mkdir -p "$(dirname "$long_tb")"
make_long_stall_tb "$long_tb"
make_scheduler_safe_tb "$TEST_ROOT/aer_tb.sv" "$safe_tb"
make_scheduler_safe_tb "$long_tb" "$safe_long_tb"

for sources in $SOURCE_COUNTS; do
  case "$SIMULATOR" in
    verilator)
      run_verilator_aer active "$sources" "$TEST_ROOT/aer_tb.sv"
      ;;
    xrun)
      run_xrun_aer active "$sources" "$TEST_ROOT/aer_tb.sv"
      ;;
    *) printf 'unsupported HYEONSU_SIMULATOR=%s\n' "$SIMULATOR" >&2; exit 2 ;;
  esac
done

if [[ "$RUN_SCHEDULER_SAFE" == 1 ]]; then
  for sources in $SOURCE_COUNTS; do
    case "$SIMULATOR" in
      verilator) run_verilator_aer active_scheduler_safe "$sources" "$safe_tb" ;;
      xrun) run_xrun_aer active_scheduler_safe "$sources" "$safe_tb" ;;
    esac
  done
fi

if [[ "$RUN_LONG_STALL" == 1 ]]; then
  for sources in $SOURCE_COUNTS; do
    case "$SIMULATOR" in
      verilator) run_verilator_aer long_stall "$sources" "$long_tb" ;;
      xrun) run_xrun_aer long_stall "$sources" "$long_tb" ;;
    esac
  done
  if [[ "$RUN_SCHEDULER_SAFE" == 1 ]]; then
    for sources in $SOURCE_COUNTS; do
      case "$SIMULATOR" in
        verilator) run_verilator_aer long_stall_scheduler_safe "$sources" "$safe_long_tb" ;;
        xrun) run_xrun_aer long_stall_scheduler_safe "$sources" "$safe_long_tb" ;;
      esac
    done
  fi
fi

if [[ "$RUN_ARBITER" == 1 ]]; then
  case "$SIMULATOR" in
    verilator) run_verilator_arbiter ;;
    xrun) run_xrun_arbiter ;;
  esac
fi

if [[ "$RUN_NATIVE" == 1 ]]; then
  native_root="$RESULTS_ROOT/native"
  mkdir -p "$native_root"
  set +e
  AER_SIM_OUT="$native_root" "$PROJECT_ROOT/scripts/run_a23_ee430_checks.sh" \
    > "$native_root/ee430-checks.log" 2>&1
  native_checks_status=$?
  AER_SIM_OUT="$native_root" "$PROJECT_ROOT/scripts/run_a23_functional_checks.sh" \
    > "$native_root/functional.log" 2>&1
  native_functional_status=$?
  A23_RESULTS_ROOT="$native_root/stress" A23_SEEDS="1 2" \
    A23_SIMULATORS="${HYEONSU_NATIVE_STRESS_SIMULATORS:-iverilog verilator}" \
    "$PROJECT_ROOT/scripts/run_a23_stress.sh" > "$native_root/stress.log" 2>&1
  native_stress_status=$?
  set -e
  record_status native_ee430 all NA "$([[ $native_checks_status -eq 0 ]] && printf PASS || printf 'FAIL(%s)' "$native_checks_status")" "$native_root/ee430-checks.log"
  record_status native_functional all NA "$([[ $native_functional_status -eq 0 ]] && printf PASS || printf 'FAIL(%s)' "$native_functional_status")" "$native_root/functional.log"
  record_status native_stress all NA "$([[ $native_stress_status -eq 0 ]] && printf PASS || printf 'FAIL(%s)' "$native_stress_status")" "$native_root/stress.log"
fi

printf 'Hyeonsu/A23 compatibility run complete: %s\n' "$RESULTS_ROOT"
column -t -s $'\t' "$STATUS_TSV" 2>/dev/null || sed -n '1,200p' "$STATUS_TSV"
