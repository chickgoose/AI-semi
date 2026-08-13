foreach name {CORE_TOP CORE_SOURCES CORE_SDC CORE_SETUP_LIB CORE_GENUS_OUT} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "missing required environment variable $name"
  }
}

set top       $::env(CORE_TOP)
set sources   $::env(CORE_SOURCES)
set sdc       [file normalize $::env(CORE_SDC)]
set library   [file normalize $::env(CORE_SETUP_LIB)]
set output    [file normalize $::env(CORE_GENUS_OUT)]

foreach path [concat $sources [list $sdc $library]] {
  if {![file isfile $path]} {
    error "required Genus input is not a regular file: $path"
  }
}

file mkdir "$output/reports"
file mkdir "$output/netlist"

set_db library $library
set_db lp_insert_clock_gating true
read_hdl -v {*}$sources
elaborate $top
read_sdc $sdc

syn_generic
syn_map
syn_opt

report_area > "$output/reports/area.rpt"
report_timing > "$output/reports/timing.rpt"
report_power > "$output/reports/power_vectorless.rpt"
report_qor > "$output/reports/qor.rpt"
check_timing_intent -verbose > "$output/reports/timing_intent.rpt"
report_clocks -generated -uncertainty_table > "$output/reports/clocks.rpt"

write_hdl > "$output/netlist/${top}.mapped.v"
write_sdc > "$output/netlist/${top}.mapped.sdc"
write_sdf > "$output/netlist/${top}.mapped.sdf"

puts "K2_CORE_GENUS_COMMANDS_COMPLETE top=$top"
exit 0
