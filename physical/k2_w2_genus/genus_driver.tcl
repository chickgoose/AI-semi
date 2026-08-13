foreach required {W2_TOP W2_SOURCES_V W2_SOURCES_SV W2_DEFINES W2_LIBRARY W2_SDC W2_OUTPUT} {
  if {![info exists ::env($required)]} {
    error "missing required environment variable $required"
  }
}

set DESIGN  $::env(W2_TOP)
set SDC_FILE $::env(W2_SDC)
set LIB_FILE $::env(W2_LIBRARY)
set OUT_DIR  $::env(W2_OUTPUT)
set defines  $::env(W2_DEFINES)
file mkdir $OUT_DIR

# These commands and their order are rebased on the SHA-pinned Ganghee
# resynthesis genus_1.0.tcl files. Safety checks are performed by the wrapper
# over the log, reports, and mapped netlist rather than by adding a flow mode
# absent from the golden scripts.
set_db library $LIB_FILE
set_db lp_insert_clock_gating true

if {$::env(W2_SOURCES_V) ne ""} {
  read_hdl -v -define $defines {*}$::env(W2_SOURCES_V)
}
if {$::env(W2_SOURCES_SV) ne ""} {
  read_hdl -sv -define $defines {*}$::env(W2_SOURCES_SV)
}
elaborate $DESIGN
read_sdc $SDC_FILE

syn_generic
syn_map
syn_opt

report_area   > $OUT_DIR/${DESIGN}_area.rpt
report_timing > $OUT_DIR/${DESIGN}_gtiming.rpt
report_power  > $OUT_DIR/${DESIGN}_gpower.rpt

write_hdl > $OUT_DIR/${DESIGN}_netlist.v
write_sdc > $OUT_DIR/${DESIGN}_out.sdc
write_sdf > $OUT_DIR/${DESIGN}.sdf

puts "W2_GENUS_PASS top=$DESIGN"
exit
