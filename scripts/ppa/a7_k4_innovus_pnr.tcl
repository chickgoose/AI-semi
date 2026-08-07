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

set init_lef_file [list $tech_lef $macro_lef]
set init_verilog $netlist
set init_top_cell $top
set init_gnd_net VSS
set init_pwr_net VDD
set init_mmmc_file $mmmc

init_design
setDesignMode -process 45

# Freeze the same physical policy for both designs.  The generous 50% target
# avoids rewarding one candidate for a candidate-specific floorplan squeeze.
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
verify_drc -report "$output/reports/drc.rpt"
verify_process_antenna -report "$output/reports/antenna.rpt"
reportRoute > "$output/reports/route.rpt"
saveDesign "$output/database/${top}.enc"
exit
