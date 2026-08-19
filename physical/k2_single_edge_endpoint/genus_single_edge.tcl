proc se_require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "missing required environment variable $name"
  }
  return $::env($name)
}

foreach name {SE_TOP SE_PROJECT_ROOT SE_FILELIST SE_SDC SE_SETUP_LIB SE_GENUS_OUT} {
  se_require_env $name
}
foreach path [list $::env(SE_FILELIST) $::env(SE_SDC) $::env(SE_SETUP_LIB)] {
  if {![file isfile $path]} { error "required Genus input is not a regular file: $path" }
}

set top [se_require_env SE_TOP]
set output [file normalize [se_require_env SE_GENUS_OUT]]
file mkdir "$output/reports"
file mkdir "$output/netlist"
file mkdir "$output/status"

set_db library [file normalize $::env(SE_SETUP_LIB)]
set_db lp_insert_clock_gating false
cd [file normalize $::env(SE_PROJECT_ROOT)]
read_hdl -sv -define SYNTHESIS -f [file normalize $::env(SE_FILELIST)]
elaborate $top
read_sdc [file normalize $::env(SE_SDC)]

set clocks [get_clocks *]
if {[sizeof_collection $clocks] != 1 ||
    [get_object_name $clocks] ne "se_primary_clk"} {
  error "expected exactly the single primary endpoint clock"
}

check_design -all > "$output/reports/check_design.rpt"
check_timing_intent -verbose > "$output/reports/timing_intent.rpt"
syn_generic
syn_map
syn_opt

report_area > "$output/reports/area.rpt"
report_timing -max_paths 50 > "$output/reports/timing.rpt"
report_qor > "$output/reports/qor.rpt"
# This report is screening-only because all boundary activity is default and
# the I/O/load values are contract-marked placeholders.
report_power > "$output/reports/power_vectorless_screening.rpt"
report_clocks -generated -uncertainty_table > "$output/reports/clocks.rpt"

write_hdl > "$output/netlist/${top}.mapped.v"
write_sdc > "$output/netlist/${top}.mapped.sdc"
write_sdf > "$output/netlist/${top}.mapped.sdf"
set marker [open "$output/status/COMMANDS_COMPLETE" {WRONLY CREAT EXCL}]
puts $marker "K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top=$top"
close $marker
puts "K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top=$top"
exit 0
