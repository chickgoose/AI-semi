proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

set top       [require_env AER_TOP]
set filelist  [require_env AER_RTL_FILELIST]
set sdc       [require_env AER_SDC]
set library   [require_env AER_LIBRARY_FILE]
set output    [require_env AER_OUTPUT_DIR]
set sources   [require_env AER_NUM_SOURCES]
set addr_w    [require_env AER_ADDR_WIDTH]

file mkdir "$output/reports"
file mkdir "$output/netlist"

set_db init_lib_search_path [file dirname $library]
set_db library [list $library]
set_db syn_generic_effort medium
set_db syn_map_effort medium
set_db syn_opt_effort medium

read_hdl -sv -f $filelist
set parameters [list $sources $addr_w]
elaborate $top -parameters $parameters
check_design -unresolved > "$output/reports/check_unresolved.rpt"
read_sdc $sdc

syn_generic
syn_map
syn_opt

check_design  > "$output/reports/check_design.rpt"
report_qor    > "$output/reports/qor.rpt"
report_area   > "$output/reports/area.rpt"
report_timing > "$output/reports/timing.rpt"
report_power  > "$output/reports/power.rpt"

write_hdl > "$output/netlist/${top}.v"
write_sdc > "$output/netlist/${top}.sdc"
exit
