proc core_require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "missing required environment variable $name"
  }
  return $::env($name)
}

proc core_positive {name} {
  set value [core_require_env $name]
  if {![string is double -strict $value] || $value <= 0.0} {
    error "$name must be a positive number"
  }
  return $value
}

proc core_timing_summary {path view check} {
  set paths [report_timing -collection -view $view -check_type $check \
    -max_paths 1000000]
  set path_count [sizeof_collection $paths]
  if {$path_count == 0} { error "no $check paths in $view" }
  set violations 0
  set wns ""
  set tns 0.0
  foreach_in_collection timing_path $paths {
    set slack [get_db $timing_path .slack]
    if {$wns eq "" || $slack < $wns} { set wns $slack }
    if {$slack < 0.0} {
      incr violations
      set tns [expr {$tns + $slack}]
    }
  }
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_core_timing_summary_v1"
  puts $handle "view=$view"
  puts $handle "check=$check"
  puts $handle "path_count=$path_count"
  puts $handle "violation_count=$violations"
  puts $handle "wns=$wns"
  puts $handle "tns=$tns"
  close $handle
  if {$violations != 0 || $wns < 0.0 || $tns != 0.0} {
    error "$check timing is not closed in $view"
  }
}

foreach name {
  CORE_TOP CORE_MAPPED_NETLIST CORE_MAPPED_SDC CORE_TECH_LEF CORE_MACRO_LEF
  CORE_MMMC CORE_INNOVUS_OUT CORE_SITE CORE_PROCESS CORE_ASPECT CORE_UTIL
  CORE_MARGIN CORE_VDD CORE_VSS CORE_RING_H CORE_RING_V CORE_RING_WIDTH
  CORE_RING_SPACING CORE_RING_OFFSET
} {
  core_require_env $name
}

set top       $::env(CORE_TOP)
set netlist   [file normalize $::env(CORE_MAPPED_NETLIST)]
set tech_lef  [file normalize $::env(CORE_TECH_LEF)]
set macro_lef [file normalize $::env(CORE_MACRO_LEF)]
set mmmc      [file normalize $::env(CORE_MMMC)]
set output    [file normalize $::env(CORE_INNOVUS_OUT)]
set aspect    [core_positive CORE_ASPECT]
set util      [core_positive CORE_UTIL]
set margin    [core_positive CORE_MARGIN]

if {$util <= 0.0 || $util >= 1.0} {
  error "CORE_UTIL must be strictly between zero and one"
}
foreach path [list $netlist $::env(CORE_MAPPED_SDC) $tech_lef $macro_lef $mmmc] {
  if {![file isfile $path]} { error "required Innovus input is not a regular file: $path" }
}

file mkdir "$output/reports"
file mkdir "$output/netlist"
file mkdir "$output/database"
file mkdir "$output/status"

set init_lef_file [list $tech_lef $macro_lef]
set init_verilog $netlist
set init_top_cell $top
set init_gnd_net $::env(CORE_VSS)
set init_pwr_net $::env(CORE_VDD)
set init_mmmc_file $mmmc

set flow_failed [catch {
  init_design
  setDesignMode -process $::env(CORE_PROCESS)
  setAnalysisMode -analysisType onChipVariation -cppr both

  # Keep both rows on the same single-height CoreSite policy.  The pinned
  # gsclib045 BUFX2 declares an incompatible site; replace mapped occurrences
  # before placement and prevent later optimization from reintroducing it.
  set bufx2_cells [get_db base_cells -if {.name == BUFX2}]
  set bufx4_cells [get_db base_cells -if {.name == BUFX4}]
  if {[llength $bufx2_cells] != 1 || [llength $bufx4_cells] != 1} {
    error "required BUFX2/BUFX4 cells are not uniquely available"
  }
  if {[get_db [lindex $bufx4_cells 0] .site.name] ne $::env(CORE_SITE)} {
    error "BUFX4 replacement does not use the contract site"
  }
  foreach instance [get_db insts -if {.base_cell.name == BUFX2}] {
    ecoChangeCell -inst [get_db $instance .name] -cell BUFX4
  }
  if {[llength [get_db insts -if {.base_cell.name == BUFX2}]] != 0} {
    error "BUFX2 instances remain after site normalization"
  }
  setDontUse BUFX2 true

  floorPlan -r $aspect $util $margin $margin $margin $margin
  set rows [dbGet top.fPlan.rows.name]
  if {[llength $rows] == 0} { error "floorplan created no placement rows" }
  set row_sites [dbGet top.fPlan.rows.site.name -u]
  foreach row_site $row_sites {
    if {$row_site ne $::env(CORE_SITE)} {
      error "floorplan row uses non-contract site $row_site"
    }
  }
  foreach instance_site [lsort -unique [get_db insts .base_cell.site.name]] {
    if {$instance_site ne $::env(CORE_SITE)} {
      error "mapped instance uses non-contract site $instance_site"
    }
  }
  set floorplan [open "$output/reports/floorplan.machine" {WRONLY CREAT EXCL}]
  puts $floorplan "schema=k2_core_floorplan_receipt_v1"
  puts $floorplan "aspect_ratio=$aspect"
  puts $floorplan "core_utilization=$util"
  puts $floorplan "core_margin_um=$margin"
  puts $floorplan "site=$::env(CORE_SITE)"
  puts $floorplan "row_count=[llength $rows]"
  puts $floorplan "row_sites=[join $row_sites ,]"
  puts $floorplan "core_bbox=[get_db current_design .core_bbox]"
  close $floorplan

  set all_io [get_db ports .name]
  if {[llength $all_io] == 0} { error "top has no IO ports" }
  editPin -pin $all_io -side Left -layer Metal3 -spreadType side

  globalNetConnect $::env(CORE_VDD) -type pgpin -pin $::env(CORE_VDD) -inst * -verbose
  globalNetConnect $::env(CORE_VSS) -type pgpin -pin $::env(CORE_VSS) -inst * -verbose
  addRing -nets [list $::env(CORE_VDD) $::env(CORE_VSS)] -type core_rings \
    -layer [list top $::env(CORE_RING_H) bottom $::env(CORE_RING_H) \
                 left $::env(CORE_RING_V) right $::env(CORE_RING_V)] \
    -width $::env(CORE_RING_WIDTH) -spacing $::env(CORE_RING_SPACING) \
    -offset $::env(CORE_RING_OFFSET)

  redirect -file "$output/reports/check_design_pre_place.rpt" {checkDesign -all}
  place_opt_design
  redirect -file "$output/reports/check_place.rpt" {checkPlace}
  clock_opt_design
  routeDesign
  extractRC
  optDesign -postRoute
  optDesign -postRoute -hold
  sroute -nets [list $::env(CORE_VDD) $::env(CORE_VSS)] \
    -connect {blockPin padPin corePin}
  editTrim -nets [list $::env(CORE_VDD) $::env(CORE_VSS)]
  extractRC

  setAnalysisMode -checkType setup
  report_timing -view core_setup_view -check_type setup -max_paths 50 \
    > "$output/reports/setup_timing.rpt"
  core_timing_summary "$output/reports/setup_timing.machine" core_setup_view setup
  setAnalysisMode -checkType hold
  report_timing -view core_hold_view -check_type hold -max_paths 50 \
    > "$output/reports/hold_timing.rpt"
  core_timing_summary "$output/reports/hold_timing.machine" core_hold_view hold
  setAnalysisMode -checkType setup

  report_area > "$output/reports/area.rpt"
  report_power > "$output/reports/power_vectorless.rpt"
  reportCongestion > "$output/reports/congestion.rpt"
  redirect -file "$output/reports/check_timing.rpt" {check_timing -verbose}
  redirect -file "$output/reports/check_design_post_route.rpt" {checkDesign -all}
  verifyConnectivity -type all -error 1000 -warning 1000 \
    -report "$output/reports/connectivity.rpt"
  verifyConnectivity -type special -error 1000 -warning 1000 \
    -report "$output/reports/pg_connectivity.rpt"
  verify_drc -report "$output/reports/drc.rpt"
  verify_process_antenna -report "$output/reports/antenna.rpt"
  reportRoute > "$output/reports/route.rpt"

  saveNetlist "$output/netlist/${top}.postroute.v"
  write_sdf "$output/netlist/${top}.postroute.sdf"
  rcOut -spef "$output/netlist/${top}.postroute.spef"
  saveDesign -mmmc2 "$output/database/${top}.enc"

  set marker [open "$output/status/COMMANDS_COMPLETE" {WRONLY CREAT EXCL}]
  puts $marker "K2_CORE_INNOVUS_COMMANDS_COMPLETE top=$top"
  close $marker
} flow_error flow_options]

if {$flow_failed} {
  set failed_path "$output/status/COMMANDS_FAILED"
  if {![file exists $failed_path]} {
    set failed [open $failed_path {WRONLY CREAT EXCL}]
    puts $failed $flow_error
    close $failed
  }
  puts stderr "K2_CORE_INNOVUS_FATAL: $flow_error"
  if {[dict exists $flow_options -errorinfo]} {
    puts stderr [dict get $flow_options -errorinfo]
  }
  exit 1
}
exit 0
