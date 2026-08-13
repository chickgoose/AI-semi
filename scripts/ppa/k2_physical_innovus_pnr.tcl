proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

proc positive_number {name} {
  set value [require_env $name]
  if {![string is double -strict $value] || $value <= 0.0} {
    error "$name must be a positive number"
  }
  return $value
}

proc write_failure_marker {output message} {
  set path "$output/status/COMMANDS_FAILED"
  if {![file exists $path]} {
    set handle [open $path {WRONLY CREAT EXCL}]
    puts $handle $message
    close $handle
  }
}

set top       [require_env AER_TOP]
set netlist   [file normalize [require_env AER_PNR_NETLIST]]
set tech_lef  [file normalize [require_env AER_TECH_LEF]]
set cell_lef  [file normalize [require_env AER_CELL_LEF]]
set mmmc      [file normalize [require_env AER_PNR_MMMC]]
set io_file   [file normalize [require_env AER_IO_FILE]]
set output    [file normalize [require_env AER_PNR_OUTPUT_DIR]]
set site      [require_env AER_CORE_SITE]
set process   [require_env AER_PROCESS_NODE_NM]
set aspect    [positive_number AER_CORE_ASPECT_RATIO]
set util      [positive_number AER_CORE_UTILIZATION]
set margin    [positive_number AER_CORE_MARGIN_UM]
set vdd       [require_env AER_VDD_NET]
set vss       [require_env AER_VSS_NET]
set vdd_pin   [require_env AER_VDD_PIN]
set vss_pin   [require_env AER_VSS_PIN]
set ring_h    [require_env AER_RING_HORIZONTAL_LAYER]
set ring_v    [require_env AER_RING_VERTICAL_LAYER]
set ring_w    [positive_number AER_RING_WIDTH_UM]
set ring_s    [positive_number AER_RING_SPACING_UM]
set ring_o    [positive_number AER_RING_OFFSET_UM]

if {$util <= 0.0 || $util >= 1.0} {
  error "AER_CORE_UTILIZATION must be strictly between zero and one"
}

foreach path [list $netlist $tech_lef $cell_lef $mmmc $io_file] {
  if {![file isfile $path]} {
    error "required physical input is not a regular file: $path"
  }
}

file mkdir "$output/reports"
file mkdir "$output/database"
file mkdir "$output/netlist"
file mkdir "$output/status"

set lef_files [list $tech_lef $cell_lef]
if {[info exists ::env(AER_EXTRA_LEFS)] && $::env(AER_EXTRA_LEFS) ne ""} {
  foreach path $::env(AER_EXTRA_LEFS) {
    set normalized [file normalize $path]
    if {![file isfile $normalized]} {
      error "AER_EXTRA_LEFS entry is not a regular file: $normalized"
    }
    lappend lef_files $normalized
  }
}

set init_lef_file $lef_files
set init_verilog $netlist
set init_top_cell $top
set init_gnd_net $vss
set init_pwr_net $vdd
set init_mmmc_file $mmmc
set init_io_file $io_file

set flow_failed [catch {
  init_design
  setDesignMode -process $process

  # OCV is common to both candidates and CPPR is enabled symmetrically.
  setAnalysisMode -analysisType onChipVariation -cppr both

  # An explicit library site is mandatory.  The old implicit floorPlan call
  # could create no legal rows or choose a different site across bundles.
  floorPlan -site $site -r $aspect $util $margin $margin $margin $margin
  set row_names [dbGet top.fPlan.rows.name]
  if {[llength $row_names] == 0} {
    error "floorplan created no standard-cell rows for site $site"
  }
  foreach row_site [dbGet top.fPlan.rows.site.name -u] {
    if {$row_site ne $site} {
      error "floorplan row uses site $row_site instead of frozen site $site"
    }
  }

  # Connect both ordinary PG pins and tie cells before building the common ring.
  globalNetConnect $vdd -type pgpin -pin $vdd_pin -inst * -verbose
  globalNetConnect $vss -type pgpin -pin $vss_pin -inst * -verbose
  globalNetConnect $vdd -type tiehi -inst * -verbose
  globalNetConnect $vss -type tielo -inst * -verbose
  applyGlobalNets
  addRing -nets [list $vdd $vss] -type core_rings \
    -layer [list top $ring_h bottom $ring_h left $ring_v right $ring_v] \
    -width $ring_w -spacing $ring_s -offset $ring_o
  sroute -nets [list $vdd $vss] -connect {corePin blockPin padPin floatingStripe}

  redirect -file "$output/reports/check_design_pre_place.rpt" {checkDesign -all}
  verifyConnectivity -type special -error 1000 -warning 1000 \
    -report "$output/reports/pg_connectivity.rpt"

  place_opt_design
  redirect -file "$output/reports/check_place_post_place.rpt" {checkPlace}
  clock_opt_design
  routeDesign
  extractRC
  set_propagated_clock [all_clocks]

  redirect -file "$output/reports/check_place_post_route.rpt" {checkPlace}
  report_area > "$output/reports/area.rpt"
  report_power > "$output/reports/power.rpt"
  report_timing -view w2_view_setup -check_type setup -max_paths 50 \
    > "$output/reports/setup_timing.rpt"
  report_timing -view w2_view_hold -check_type hold -max_paths 50 \
    > "$output/reports/hold_timing.rpt"
  report_timing -view w2_view_setup -check_type recovery -max_paths 50 \
    > "$output/reports/recovery_timing.rpt"
  report_timing -view w2_view_hold -check_type removal -max_paths 50 \
    > "$output/reports/removal_timing.rpt"
  redirect -file "$output/reports/check_timing.rpt" {check_timing -verbose}
  redirect -file "$output/reports/check_design_post_route.rpt" {checkDesign -all}
  verifyConnectivity -type all -error 1000 -warning 1000 \
    -report "$output/reports/connectivity.rpt"
  verifyConnectivity -type special -error 1000 -warning 1000 \
    -report "$output/reports/pg_connectivity_post_route.rpt"
  verify_drc -report "$output/reports/drc.rpt"
  verify_process_antenna -report "$output/reports/antenna.rpt"
  reportRoute > "$output/reports/route.rpt"

  # Both the database and a portable post-route netlist are authoritative W2
  # artifacts.  saveDesign alone is not a substitute for saveNetlist.
  saveNetlist "$output/netlist/${top}.postroute.v"
  saveDesign "$output/database/${top}.enc"

  set marker [open "$output/status/COMMANDS_COMPLETE" {WRONLY CREAT EXCL}]
  puts $marker "W2_INNOVUS_COMMANDS_COMPLETE"
  close $marker
} flow_error flow_options]

if {$flow_failed} {
  write_failure_marker $output $flow_error
  puts stderr "W2_INNOVUS_FLOW_FATAL: $flow_error"
  if {[dict exists $flow_options -errorinfo]} {
    puts stderr [dict get $flow_options -errorinfo]
  }
  exit 1
}

# FLOW_CLEAN is intentionally never written by Innovus.  The independent
# report verifier creates it only after all physical and timing gates pass.
exit 0
