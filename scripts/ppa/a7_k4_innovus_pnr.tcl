proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

set top       [require_env AER_TOP]
set netlist   [require_env AER_PNR_NETLIST]
set tech_lef  [require_env AER_TECH_LEF]
set macro_lef [require_env AER_MACRO_LEF]
set mmmc      [require_env AER_PNR_MMMC]
set output    [require_env AER_PNR_OUTPUT_DIR]

file mkdir "$output/reports"
file mkdir "$output/database"
file mkdir "$output/status"

set init_lef_file [list $tech_lef $macro_lef]
set init_verilog $netlist
set init_top_cell $top
set init_gnd_net VSS
set init_pwr_net VDD
set init_mmmc_file $mmmc

set flow_failed [catch {
  init_design
  setDesignMode -process 45

  # Freeze the same physical policy for both designs.  The generous 50% target
  # avoids rewarding one candidate for a candidate-specific floorplan squeeze.
  # Pin placement is intentionally not claimed as qualified until a reviewed,
  # deterministic common pin constraint is available for all 376 boundary bits.
  floorPlan -r 1.0 0.50 10 10 10 10
  globalNetConnect VDD -type pgpin -pin VDD -inst * -verbose
  globalNetConnect VSS -type pgpin -pin VSS -inst * -verbose
  addRing -nets {VDD VSS} -type core_rings \
    -layer {top Metal6 bottom Metal6 left Metal7 right Metal7} \
    -width 2 -spacing 2 -offset 2
  sroute -nets {VDD VSS} -connect {blockPin padPin corePin}

  checkDesign -all > "$output/reports/check_design_pre_place.rpt"
  place_opt_design
  clock_opt_design
  routeDesign
  extractRC

  report_area > "$output/reports/area.rpt"
  report_power > "$output/reports/power.rpt"
  report_timing -late > "$output/reports/setup_timing.rpt"
  report_timing -early > "$output/reports/hold_timing.rpt"
  check_timing -verbose > "$output/reports/check_timing.rpt"
  checkDesign -all > "$output/reports/check_design_post_route.rpt"
  verifyConnectivity -type all -error 1000 -warning 1000 \
    -report "$output/reports/connectivity.rpt"
  verify_drc -report "$output/reports/drc.rpt"
  verify_process_antenna -report "$output/reports/antenna.rpt"
  reportRoute > "$output/reports/route.rpt"
  saveDesign "$output/database/${top}.enc"

  # The runner requires this sentinel.  Its presence means every command above
  # returned normally, not that timing, DRC, antenna, pins, or hold are qualified.
  set success_file [open "$output/status/FLOW_SUCCESS" w]
  puts $success_file "A7_K4_INNOVUS_COMMAND_SEQUENCE_COMPLETE"
  close $success_file
} flow_error flow_options]

if {$flow_failed} {
  puts stderr "AER_INNOVUS_FLOW_FATAL: $flow_error"
  if {[dict exists $flow_options -errorinfo]} {
    puts stderr [dict get $flow_options -errorinfo]
  }
  exit 1
}
exit 0
