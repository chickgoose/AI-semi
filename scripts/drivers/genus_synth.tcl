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
set variant   [require_env AER_VARIANT]
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
if {$variant eq "improved"} {
  lappend parameters [require_env AER_FIFO_DEPTH]
}
elaborate $top -parameters $parameters
check_design -unresolved
read_sdc $sdc

syn_generic
syn_map
syn_opt

report_qor    > "$output/reports/qor.rpt"
report_area   > "$output/reports/area.rpt"
report_timing > "$output/reports/timing.rpt"

# Optional site-owned Tcl may annotate the common VCD/SAIF workload. It can
# read ::env(AER_POWER_ACTIVITY). Without it Genus reports vectorless power.
if {[info exists ::env(AER_GENUS_ACTIVITY_TCL)] &&
    $::env(AER_GENUS_ACTIVITY_TCL) ne ""} {
  source $::env(AER_GENUS_ACTIVITY_TCL)
}
report_power  > "$output/reports/power.rpt"

write_hdl > "$output/netlist/${top}.v"
write_sdc > "$output/netlist/${top}.sdc"
exit
