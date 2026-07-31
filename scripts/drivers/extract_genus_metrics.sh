#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { printf 'usage: %s <Genus-output-dir> <clock-period-ns>\n' "$0" >&2; exit 2; }
output_dir="$1"
clock_period="$2"
area_report="$output_dir/reports/area.rpt"
timing_report="$output_dir/reports/timing.rpt"
qor_report="$output_dir/reports/qor.rpt"
power_report="$output_dir/reports/power.rpt"

for report in "$area_report" "$timing_report" "$qor_report" "$power_report"; do
  [[ -s "$report" ]] || { printf 'missing or empty report: %s\n' "$report" >&2; exit 2; }
done

extract_number() {
  local pattern="$1"
  shift
  awk -v pattern="$pattern" '
    tolower($0) ~ pattern {
      for (i = NF; i >= 1; i--) {
        token = $i
        gsub(/[,;:=()]/, "", token)
        if (token ~ /^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$/) {
          print token
          exit
        }
      }
    }
  ' "$@"
}

first_or_na() {
  local value="$1"
  if [[ -n "$value" ]]; then printf '%s\n' "$value"; else printf 'N/A\n'; fi
}

extract_qor_area() {
  awk '
    /Cell Area.*Physical Cell Area.*Total Cell Area/ {in_area = 1; next}
    in_area {
      for (i = 1; i <= NF; i++) {
        token = $i; gsub(/,/, "", token)
        if (token ~ /^[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$/) {
          print token; exit
        }
      }
    }
  ' "$qor_report"
}

extract_qor_timing() {
  local wanted="$1"
  awk -v wanted="$wanted" '
    /Critical.*Slack.*TNS.*Paths|Group.*Path.*Slack.*TNS.*Paths/ {in_timing = 1; next}
    in_timing && /Instance Count|Area & Power/ {in_timing = 0}
    in_timing {
      count = 0
      for (i = 1; i <= NF; i++) {
        token = $i; gsub(/,/, "", token)
        if (token ~ /^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$/) {
          count++; number[count] = token
        }
      }
      if (count >= 3) {
        slack = number[count-2]; tns = number[count-1]
        if (!seen || slack < worst) worst = slack
        total_tns += tns
        seen = 1
      }
    }
    END {
      if (seen && wanted == "wns") print worst
      if (seen && wanted == "tns") print total_tns
    }
  ' "$qor_report"
}

area="$(extract_number 'total[[:space:]_]+(cell[[:space:]_]+)?area|cell[[:space:]_]+area' "$area_report" "$qor_report")"
wns="$(extract_number '(^|[^a-z])wns([^a-z]|$)|worst.*slack|slack.*worst' "$qor_report" "$timing_report")"
tns="$(extract_number '(^|[^a-z])tns([^a-z]|$)|total.*negative.*slack' "$qor_report" "$timing_report")"
[[ -n "$area" ]] || area="$(extract_qor_area)"
[[ -n "$wns" ]] || wns="$(extract_qor_timing wns)"
[[ -n "$tns" ]] || tns="$(extract_qor_timing tns)"
total_power="$(extract_number 'total[[:space:]_]+power' "$power_report")"
dynamic_power="$(extract_number 'dynamic[[:space:]_]+power|total[[:space:]_]+dynamic' "$power_report")"
leakage_power="$(extract_number 'leakage[[:space:]_]+power|total[[:space:]_]+leakage' "$power_report")"

fmax="N/A"
if [[ -n "$wns" ]]; then
  fmax="$(awk -v period="$clock_period" -v slack="$wns" 'BEGIN {
    delay = period - slack
    if (delay > 0) printf "%.6f", 1000.0 / delay; else print "N/A"
  }')"
fi

{
  printf 'metric\tvalue\tunit\n'
  printf 'cell_area\t%s\tum2\n' "$(first_or_na "$area")"
  printf 'wns\t%s\tns\n' "$(first_or_na "$wns")"
  printf 'tns\t%s\tns\n' "$(first_or_na "$tns")"
  printf 'fmax\t%s\tMHz\n' "$fmax"
  # Confirm the unit printed in the server report before treating these as mW.
  printf 'total_power\t%s\tmW\n' "$(first_or_na "$total_power")"
  printf 'dynamic_power\t%s\tmW\n' "$(first_or_na "$dynamic_power")"
  printf 'leakage_power\t%s\tmW\n' "$(first_or_na "$leakage_power")"
} > "$output_dir/metrics.tsv"

if [[ "$area" == "" || "$wns" == "" || "$tns" == "" || "$total_power" == "" ]]; then
  printf 'warning: one or more metrics were not recognized; inspect reports and N/A fields\n' >&2
fi
