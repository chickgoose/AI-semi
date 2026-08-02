#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 4 ]] || {
  printf 'usage: %s <output-dir> <library> <top> <throughput>\n' "$0" >&2
  exit 2
}
output_dir="$1"
library="$2"
top="$3"
throughput="$4"
netlist="$output_dir/netlist/$top.v"
timing="$output_dir/reports/timing.rpt"
tool_log="$output_dir/tool.log"
check_unresolved="$output_dir/reports/check_unresolved.rpt"

for path in "$library" "$netlist" "$timing" "$tool_log" "$check_unresolved"; do
  [[ -s "$path" ]] || { printf 'missing detail input: %s\n' "$path" >&2; exit 2; }
done

cell_counts="$(awk '
  FNR == NR {
    if (match($0, /^[[:space:]]*cell[[:space:]]*\([[:space:]]*([^ )]+)/, part)) {
      current = part[1]
      library_cell[current] = 1
    }
    if ((current != "") && ($0 ~ /^[[:space:]]*ff[[:space:]]*\(/)) sequential[current] = 1
    if ((current != "") && ($0 ~ /^[[:space:]]*latch[[:space:]]*\(/)) {
      sequential[current] = 1
      latch_cell[current] = 1
    }
    next
  }
  match($0, /^[[:space:]]*([A-Za-z_][A-Za-z0-9_$]*)[[:space:]]+[^[:space:](]+[[:space:]]*\(/, part) {
    cell = part[1]
    if (cell in library_cell) {
      total++
      if (cell in sequential) seq++
      else comb++
      if (cell in latch_cell) latches++
    }
  }
  END {printf "%d\t%d\t%d\t%d", total, seq, comb, latches}
' "$library" "$netlist")"
IFS=$'\t' read -r total_cells sequential_cells combinational_cells latch_cells <<< "$cell_counts"

startpoint="$(awk -F': ' '/Startpoint:/ {print $2; exit}' "$timing")"
endpoint="$(awk -F': ' '/Endpoint:/ {print $2; exit}' "$timing")"
logic_loop_warnings="$(grep -Eic 'combinational[[:space:]_-]*loop|timing[[:space:]_-]*loop|logic[[:space:]_-]*loop' "$tool_log" || true)"
multi_driver_warnings="$(grep -Eic 'multi(ple)?[[:space:]_-]*driver|multi-driven' "$tool_log" || true)"
latch_warnings="$(grep -Eic 'inferred[[:space:]_-]*latch|latch inferred' "$tool_log" || true)"
error_lines="$(grep -Ec '^[[:space:]]*(Error|Fatal)[[:space:]:]' "$tool_log" || true)"
unresolved_count=0
if ! grep -q 'No unresolved references' "$check_unresolved"; then
  unresolved_count="$(grep -Eic 'unresolved reference' "$check_unresolved" || true)"
  [[ "$unresolved_count" -gt 0 ]] || unresolved_count=1
fi
empty_module_count=0
if ! grep -q 'No empty modules' "$check_unresolved"; then
  empty_module_count="$(grep -Eic 'empty module' "$check_unresolved" || true)"
  [[ "$empty_module_count" -gt 0 ]] || empty_module_count=1
fi

area="$(awk -F'\t' '$1 == "cell_area" {print $2}' "$output_dir/metrics.tsv")"
power="$(awk -F'\t' '$1 == "total_power" {print $2}' "$output_dir/metrics.tsv")"
area_per_event="$(awk -v value="$area" -v rate="$throughput" 'BEGIN {printf "%.6f", value/rate}')"
power_per_event="$(awk -v value="$power" -v rate="$throughput" 'BEGIN {printf "%.9f", value/rate}')"
throughput_per_area="$(awk -v value="$area" -v rate="$throughput" 'BEGIN {printf "%.9f", rate/value}')"

{
  printf 'metric\tvalue\tunit\n'
  printf 'total_cells\t%s\tcells\n' "$total_cells"
  printf 'sequential_cells\t%s\tcells\n' "$sequential_cells"
  printf 'combinational_cells\t%s\tcells\n' "$combinational_cells"
  printf 'latch_cells\t%s\tcells\n' "$latch_cells"
  printf 'critical_startpoint\t%s\ttext\n' "$startpoint"
  printf 'critical_endpoint\t%s\ttext\n' "$endpoint"
  printf 'logic_loop_warnings\t%s\tcount\n' "$logic_loop_warnings"
  printf 'multi_driver_warnings\t%s\tcount\n' "$multi_driver_warnings"
  printf 'latch_warnings\t%s\tcount\n' "$latch_warnings"
  printf 'unresolved_references\t%s\tcount\n' "$unresolved_count"
  printf 'empty_modules\t%s\tcount\n' "$empty_module_count"
  printf 'error_fatal_lines\t%s\tcount\n' "$error_lines"
  printf 'throughput\t%s\tevent_per_cycle\n' "$throughput"
  printf 'area_per_event_cycle\t%s\tum2_per_event_cycle\n' "$area_per_event"
  printf 'power_per_event_cycle\t%s\tmW_per_event_cycle\n' "$power_per_event"
  printf 'throughput_per_area\t%s\tevent_per_cycle_per_um2\n' "$throughput_per_area"
} > "$output_dir/details.tsv"
