foreach required {K2_SE_TOP K2_SE_SOURCES_SV K2_SE_LIBRARY K2_SE_SDC K2_SE_OUTPUT K2_SE_ACTIVITY_MODE} {
  if {![info exists ::env($required)] || $::env($required) eq ""} {
    error "missing required semantic environment variable $required"
  }
}
if {$::env(K2_SE_ACTIVITY_MODE) ne "GENUS_DEFAULT_VECTORLESS"} {
  error "K2_SE_ACTIVITY_MODE must be GENUS_DEFAULT_VECTORLESS"
}

set DESIGN  $::env(K2_SE_TOP)
set LIB_FILE $::env(K2_SE_LIBRARY)
set SDC_FILE $::env(K2_SE_SDC)
set OUT_DIR  $::env(K2_SE_OUTPUT)
file mkdir $OUT_DIR

set_db library $LIB_FILE
read_hdl -sv -define SYNTHESIS {*}$::env(K2_SE_SOURCES_SV)
elaborate $DESIGN
read_sdc $SDC_FILE
set clocks [get_clocks *]
if {[sizeof_collection $clocks] != 1 ||
    [get_object_name $clocks] ne "single_edge_clk"} {
  error "expected exactly one single_edge_clk primary clock"
}

syn_generic
syn_map
syn_opt
check_design -all > $OUT_DIR/${DESIGN}_check_design.rpt

report_area > $OUT_DIR/${DESIGN}_area.rpt
report_timing > $OUT_DIR/${DESIGN}_gtiming.rpt
report_power > $OUT_DIR/${DESIGN}_gpower.rpt
report_qor > $OUT_DIR/${DESIGN}_qor.rpt
check_timing_intent -verbose > $OUT_DIR/${DESIGN}_timing_intent.rpt
report_clocks -uncertainty_table > $OUT_DIR/${DESIGN}_clocks.rpt

write_hdl > $OUT_DIR/${DESIGN}_netlist.v
write_sdc > $OUT_DIR/${DESIGN}_mapped.sdc
write_sdf > $OUT_DIR/${DESIGN}.sdf

puts "K2_SINGLE_EDGE_VECTORLESS_DIAGNOSTIC_COMPLETE top=$DESIGN"
exit
