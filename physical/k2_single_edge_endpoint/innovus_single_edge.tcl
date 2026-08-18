proc se_require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "missing required environment variable $name"
  }
  return $::env($name)
}

proc se_positive {name} {
  set value [se_require_env $name]
  if {![string is double -strict $value] || $value <= 0.0} {
    error "$name must be a positive number"
  }
  return $value
}

proc se_timing_summary {path view check} {
  set paths [report_timing -collection -view $view -check_type $check \
    -max_paths 1000000]
  set path_count [sizeof_collection $paths]
  if {$path_count == 0} { error "no $check timing paths in $view" }
  set violations 0
  set wns ""
  set tns 0.0
  foreach_in_collection timing_path $paths {
    set slack [get_db $timing_path .slack]
    if {$wns eq "" || $slack < $wns} { set wns $slack }
    if {$slack < 0.0} { incr violations; set tns [expr {$tns + $slack}] }
  }
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_single_edge_timing_summary_v1"
  puts $handle "view=$view"
  puts $handle "check=$check"
  puts $handle "path_count=$path_count"
  puts $handle "violation_count=$violations"
  puts $handle "wns=$wns"
  puts $handle "tns=$tns"
  close $handle
  if {$violations != 0 || $wns < 0.0 || $tns != 0.0} {
    error "$check timing is not closed"
  }
}

proc se_append_report_context {path kind context} {
  set handle [open $path {WRONLY APPEND}]
  puts $handle "K2_SINGLE_EDGE_REPORT_CONTEXT_V1 tool=Innovus version=23.14-s088_1 top=$::env(SE_TOP) kind=$kind context=$context"
  close $handle
}

foreach name {
  SE_TOP SE_MAPPED_NETLIST SE_MAPPED_SDC SE_TECH_LEF SE_MACRO_LEF SE_MMMC
  SE_INNOVUS_OUT SE_SITE SE_PROCESS SE_ASPECT SE_UTIL SE_MARGIN
  SE_VDD SE_VSS SE_RING_H SE_RING_V SE_RING_WIDTH SE_RING_SPACING SE_RING_OFFSET
} { se_require_env $name }
foreach path [list $::env(SE_MAPPED_NETLIST) $::env(SE_MAPPED_SDC) \
                   $::env(SE_TECH_LEF) $::env(SE_MACRO_LEF) $::env(SE_MMMC)] {
  if {![file isfile $path]} { error "required Innovus input is not a regular file: $path" }
}

set top $::env(SE_TOP)
set output [file normalize $::env(SE_INNOVUS_OUT)]
set aspect [se_positive SE_ASPECT]
set util [se_positive SE_UTIL]
set margin [se_positive SE_MARGIN]
if {$util >= 1.0} { error "SE_UTIL must be less than one" }
file mkdir "$output/reports"
file mkdir "$output/netlist"
file mkdir "$output/database"
file mkdir "$output/status"

set init_lef_file [list [file normalize $::env(SE_TECH_LEF)] \
                             [file normalize $::env(SE_MACRO_LEF)]]
set init_verilog [file normalize $::env(SE_MAPPED_NETLIST)]
set init_top_cell $top
set init_gnd_net $::env(SE_VSS)
set init_pwr_net $::env(SE_VDD)
set init_mmmc_file [file normalize $::env(SE_MMMC)]

set failed [catch {
  init_design
  set_interactive_constraint_modes [list se_functional]
  set clock_ports [get_ports clk_i]
  if {[sizeof_collection $clock_ports] != 1 || [sizeof_collection [get_clocks *]] != 1} {
    error "initialized endpoint does not have exactly one primary clock"
  }
  set_drive 0 $clock_ports
  setDesignMode -process $::env(SE_PROCESS)
  setAnalysisMode -analysisType onChipVariation -cppr both

  floorPlan -r $aspect $util $margin $margin $margin $margin
  set rows [dbGet top.fPlan.rows.name]
  if {[llength $rows] == 0} { error "floorplan created no rows" }
  foreach row_site [dbGet top.fPlan.rows.site.name -u] {
    if {$row_site ne $::env(SE_SITE)} { error "non-contract placement site $row_site" }
  }
  # Pin placement is a disclosed core-boundary screening placeholder, not a
  # pad, package, signal-integrity, or organizer I/O assignment.
  set all_io [get_db ports .name]
  if {[llength $all_io] == 0} { error "top has no I/O ports" }
  editPin -pin $all_io -side Left -layer Metal3 -spreadType side

  globalNetConnect $::env(SE_VDD) -type pgpin -pin $::env(SE_VDD) -inst * -verbose
  globalNetConnect $::env(SE_VSS) -type pgpin -pin $::env(SE_VSS) -inst * -verbose
  addRing -nets [list $::env(SE_VDD) $::env(SE_VSS)] -type core_rings \
    -layer [list top $::env(SE_RING_H) bottom $::env(SE_RING_H) \
                 left $::env(SE_RING_V) right $::env(SE_RING_V)] \
    -width $::env(SE_RING_WIDTH) -spacing $::env(SE_RING_SPACING) \
    -offset $::env(SE_RING_OFFSET)

  redirect -file "$output/reports/check_design_pre_place.rpt" {checkDesign -all}
  place_opt_design
  redirect -file "$output/reports/check_place.rpt" {checkPlace}
  clock_opt_design
  routeDesign
  extractRC
  optDesign -postRoute
  optDesign -postRoute -hold
  sroute -nets [list $::env(SE_VDD) $::env(SE_VSS)] \
    -connect {blockPin padPin corePin}
  editTrim -nets [list $::env(SE_VDD) $::env(SE_VSS)]
  extractRC

  setAnalysisMode -checkType setup
  report_timing -view se_setup_view -check_type setup -max_paths 50 \
    > "$output/reports/setup_timing.rpt"
  se_append_report_context "$output/reports/setup_timing.rpt" setup_timing postroute
  se_timing_summary "$output/reports/setup_timing.machine" se_setup_view setup
  setAnalysisMode -checkType hold
  report_timing -view se_hold_view -check_type hold -max_paths 50 \
    > "$output/reports/hold_timing.rpt"
  se_append_report_context "$output/reports/hold_timing.rpt" hold_timing postroute
  se_timing_summary "$output/reports/hold_timing.machine" se_hold_view hold
  setAnalysisMode -checkType setup

  report_area > "$output/reports/area.rpt"
  report_power > "$output/reports/power_vectorless_screening.rpt"
  reportRoute > "$output/reports/route.rpt"
  redirect -file "$output/reports/check_timing.rpt" {check_timing -verbose}
  se_append_report_context "$output/reports/check_timing.rpt" check_timing postroute
  redirect -file "$output/reports/check_design_post_route.rpt" {checkDesign -all}
  verifyConnectivity -type all -error 1000 -warning 1000 \
    -report "$output/reports/connectivity.rpt"
  se_append_report_context "$output/reports/connectivity.rpt" connectivity signal_postroute
  verifyConnectivity -type special -error 1000 -warning 1000 \
    -report "$output/reports/pg_connectivity.rpt"
  se_append_report_context "$output/reports/pg_connectivity.rpt" pg_connectivity pg_postroute
  verify_drc -report "$output/reports/drc.rpt"
  se_append_report_context "$output/reports/drc.rpt" drc postroute
  verify_process_antenna -report "$output/reports/antenna.rpt"
  se_append_report_context "$output/reports/antenna.rpt" antenna postroute

  saveNetlist "$output/netlist/${top}.postroute.v"
  write_sdf "$output/netlist/${top}.postroute.sdf"
  rcOut -spef "$output/netlist/${top}.postroute.spef"
  saveDesign -mmmc2 "$output/database/${top}.enc"
  set db_manifest [open "$output/database/MANIFEST.txt" {WRONLY CREAT EXCL}]
  puts $db_manifest "design=$top checkpoint=$output/database/${top}.enc"
  puts $db_manifest "entry=saveDesign-mmmc2"
  puts $db_manifest "producer_authentication=UNAUTHENTICATED_LOCAL_SELF_HASH"
  close $db_manifest
  set marker [open "$output/status/COMMANDS_COMPLETE" {WRONLY CREAT EXCL}]
  puts $marker "K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top=$top"
  close $marker
} flow_error flow_options]

if {$failed} {
  set marker [open "$output/status/COMMANDS_FAILED" {WRONLY CREAT EXCL}]
  puts $marker $flow_error
  close $marker
  puts stderr "K2_SINGLE_EDGE_INNOVUS_FATAL: $flow_error"
  if {[dict exists $flow_options -errorinfo]} { puts stderr [dict get $flow_options -errorinfo] }
  exit 1
}
puts "K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top=$top"
exit 0
