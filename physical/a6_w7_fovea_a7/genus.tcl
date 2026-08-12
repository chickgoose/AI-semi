if {![info exists ::env(W7_DESIGN)] || ![info exists ::env(W7_SDC)] ||
    ![info exists ::env(W7_RTL_FILES)] || ![info exists ::env(W7_OUT)] ||
    ![info exists ::env(W7_LIB)]} {
  error "W7_DESIGN, W7_SDC, W7_RTL_FILES, W7_OUT, and W7_LIB are required"
}

set design $::env(W7_DESIGN)
set out $::env(W7_OUT)
file mkdir $out

set_db information_level 9
set_db library $::env(W7_LIB)
set_db lp_insert_clock_gating false
# This is not a DFT flow.  Avoid scan-prefixed sequential cells so Innovus
# cannot infer an incomplete scan chain from ordinary functional registers.
set scan_lib_cells [get_db lib_cells *SDFF*]
puts "W7_SCAN_LIB_MATCH_COUNT=[llength $scan_lib_cells]"
if {[llength $scan_lib_cells] == 0} { error "no scan-prefixed library cells matched avoidance rule" }
if {[llength $scan_lib_cells] > 0} { set_db $scan_lib_cells .avoid true }
read_hdl -sv [split $::env(W7_RTL_FILES)]
elaborate $design
read_sdc $::env(W7_SDC)
check_design -unresolved > $out/check_design.rpt
check_timing_intent > $out/check_timing_intent.rpt
syn_generic
syn_map
syn_opt

report_qor > $out/qor.rpt
report_area > $out/area.rpt
report_timing -max_paths 50 > $out/timing_setup.rpt
report_timing -unconstrained -max_paths 200 > $out/timing_unconstrained.rpt
report_power > $out/power_vectorless.rpt
report_clocks > $out/clocks.rpt
report_clock_gating > $out/clock_gating.rpt
write_hdl > $out/${design}_mapped.v
write_sdc > $out/${design}_mapped.sdc
write_db $out/${design}.db

foreach required [list \
    $out/check_design.rpt $out/check_timing_intent.rpt $out/qor.rpt \
    $out/area.rpt $out/timing_setup.rpt $out/timing_unconstrained.rpt \
    $out/${design}_mapped.v $out/${design}_mapped.sdc] {
  if {![file exists $required] || [file size $required] == 0} {
    error "empty required Genus artifact: $required"
  }
}
puts "W7_GENUS_CLEAN_END design=$design"
exit
