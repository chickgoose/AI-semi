foreach required {W2_TOP W2_SOURCES W2_DEFINES W2_LIBRARY W2_SDC W2_OUTPUT} {
  if {![info exists ::env($required)] || $::env($required) eq ""} {
    error "missing required environment variable $required"
  }
}

set top $::env(W2_TOP)
set sources $::env(W2_SOURCES)
set defines $::env(W2_DEFINES)
set output $::env(W2_OUTPUT)
file mkdir "$output/reports"

set_db library $::env(W2_LIBRARY)
set_db lp_insert_clock_gating false
read_hdl -sv -define $defines {*}$sources
elaborate $top
check_design -unresolved > "$output/reports/check_elaborated.rpt"
read_sdc $::env(W2_SDC)

syn_generic
syn_map
syn_opt

check_design -unresolved > "$output/reports/check_mapped.rpt"
report_area > "$output/reports/area.rpt"
report_qor > "$output/reports/qor.rpt"
report_timing > "$output/reports/timing.rpt"
report_clocks > "$output/reports/clocks.rpt"
report_clock_gating > "$output/reports/clock_gating.rpt"
report_power > "$output/reports/power_vectorless.rpt"
write_hdl > "$output/mapped.v"
write_sdc > "$output/mapped.sdc"

set sentinel [open "$output/genus.complete" w]
puts $sentinel "W2_GENUS_COMPLETE top=$top"
close $sentinel
puts "W2_GENUS_PASS top=$top"
exit
