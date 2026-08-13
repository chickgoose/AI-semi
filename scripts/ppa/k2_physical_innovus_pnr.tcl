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

proc write_timing_machine_summary {path view check {label ""} {targets ""}} {
  if {$label eq ""} { set label $check }
  if {$targets eq ""} {
    set paths [report_timing -collection -view $view -check_type $check \
      -max_paths 1000000]
  } else {
    set paths [report_timing -collection -view $view -check_type $check \
      -to $targets -max_paths 1000000]
  }
  set path_count [sizeof_collection $paths]
  set violation_count 0
  set tns 0.0
  set wns ""
  foreach_in_collection timing_path $paths {
    set slack [get_db $timing_path .slack]
    if {$wns eq "" || $slack < $wns} { set wns $slack }
    if {$slack < 0.0} {
      incr violation_count
      set tns [expr {$tns + $slack}]
    }
  }
  if {$path_count == 0} { error "no $check timing paths in $view" }
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_w2_timing_machine_summary_v1"
  puts $handle "check=$label"
  puts $handle "view=$view"
  puts $handle "path_count=$path_count"
  puts $handle "violation_count=$violation_count"
  puts $handle "wns=$wns"
  puts $handle "tns=$tns"
  close $handle
}

set top       [require_env AER_TOP]
set netlist   [file normalize [require_env AER_PNR_NETLIST]]
set tech_lef  [file normalize [require_env AER_TECH_LEF]]
set cell_lef  [file normalize [require_env AER_CELL_LEF]]
set mmmc      [file normalize [require_env AER_PNR_MMMC]]
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
set activity_file [file normalize [require_env AER_ACTIVITY_FILE]]
set activity_format [require_env AER_ACTIVITY_FORMAT]
set activity_scope [require_env AER_ACTIVITY_SCOPE]
set activity_start [require_env AER_ACTIVITY_WINDOW_START_NS]
set activity_end [require_env AER_ACTIVITY_WINDOW_END_NS]

if {$util <= 0.0 || $util >= 1.0} {
  error "AER_CORE_UTILIZATION must be strictly between zero and one"
}

foreach path [list $netlist $tech_lef $cell_lef $mmmc $activity_file] {
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

set flow_failed [catch {
  init_design
  setDesignMode -process $process

  # OCV is common to both candidates and CPPR is enabled symmetrically.
  setAnalysisMode -analysisType onChipVariation -cppr both

  # An explicit library site is mandatory.  The old implicit floorPlan call
  # could create no legal rows or choose a different site across bundles.
  # The server golden proves the -r form on Innovus 23.14.  Freeze the actual
  # CoreSite result by inspecting the rows immediately afterward instead of
  # relying on an unproven floorPlan option spelling.
  floorPlan -r $aspect $util $margin $margin $margin $margin
  set core_box [get_db current_design .core_bbox]
  set used_sites [lsort -unique [get_db insts .base_cell.site.name]]
  # Optimization may insert CoreSiteDouble buffers even when the incoming
  # mapped netlist contains only CoreSite cells.  Rows therefore cover both
  # the mapped inventory and the fixed PDK insertion-site inventory.
  set planned_sites [lsort -unique [concat $used_sites [list $site CoreSiteDouble]]]
  foreach used_site $planned_sites {
    if {$used_site ni [list $site CoreSiteDouble]} {
      error "mapped instance uses unsupported placement site $used_site"
    }
    if {$used_site ne $site} {
      createRow -site $used_site -area $core_box
    }
  }
  set row_names [dbGet top.fPlan.rows.name]
  if {[llength $row_names] == 0} {
    error "floorplan created no standard-cell rows for site $site"
  }
  set actual_row_sites [dbGet top.fPlan.rows.site.name -u]
  foreach planned_site $planned_sites {
    if {[lsearch -exact $actual_row_sites $planned_site] < 0} {
      error "floorplan is missing required placement rows for site $planned_site"
    }
  }
  foreach row_site $actual_row_sites {
    if {[lsearch -exact $planned_sites $row_site] < 0} {
      error "floorplan row uses unrequired site $row_site"
    }
  }

  # The Kanghee golden omitted an IO file.  Place every canonical boundary pin
  # deterministically with one identical rule instead of leaving terms
  # unplaced and allowing candidate-dependent optimizer behavior.
  set all_io [get_db ports .name]
  if {[llength $all_io] == 0} {
    error "canonical top has no IO ports"
  }
  editPin -pin $all_io -side Left -layer Metal3 -spreadType side

  # Connect both ordinary PG pins and tie cells before building the common ring.
  globalNetConnect $vdd -type pgpin -pin $vdd_pin -inst * -verbose
  globalNetConnect $vss -type pgpin -pin $vss_pin -inst * -verbose
  addRing -nets [list $vdd $vss] -type core_rings \
    -layer [list top $ring_h bottom $ring_h left $ring_v right $ring_v] \
    -width $ring_w -spacing $ring_s -offset $ring_o
  sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}

  redirect -file "$output/reports/check_design_pre_place.rpt" {checkDesign -all}
  verifyConnectivity -type special -error 1000 -warning 1000 \
    -report "$output/reports/pg_connectivity.rpt"

  place_opt_design
  set unplaced_insts [get_db insts -if {.place_status == unplaced}]
  set unplaced_ports [get_db ports -if {.place_status == unplaced}]
  if {[llength $unplaced_insts] != 0 || [llength $unplaced_ports] != 0} {
    error "placement left unplaced instances or ports"
  }
  redirect -file "$output/reports/check_place_post_place.rpt" {checkPlace}
  clock_opt_design
  routeDesign
  extractRC
  optDesign -postRoute
  optDesign -postRoute -hold
  extractRC
  # Innovus 23.14 rejects interactive constraint updates in an MMMC design
  # until their constraint mode is selected explicitly (TCLCMD-1048).  Both
  # setup and hold views intentionally share this one strict functional mode.
  set_interactive_constraint_modes [list w2_strict_functional]
  set_propagated_clock [all_clocks]
  # Preserve a resumable post-route checkpoint before activity/report parsing.
  # Later compatibility failures can then be diagnosed without rerunning P&R.
  saveDesign -mmmc2 "$output/database/${top}.postroute_checkpoint.enc"

  if {$activity_format ni {SAIF VCD}} {
    error "activity format must be SAIF or VCD"
  }
  redirect -tee -file "$output/reports/activity_annotation.rpt" {
    read_activity_file -format $activity_format -scope $activity_scope \
      -start $activity_start -end $activity_end $activity_file
  }

  redirect -file "$output/reports/check_place_post_route.rpt" {checkPlace}
  report_area > "$output/reports/area.rpt"
  report_power > "$output/reports/power.rpt"

  # Innovus 23.14 defaults interactive timing queries to late/setup analysis.
  # Select each mode explicitly before querying its compatible view/checks.
  setAnalysisMode -checkType setup
  report_timing -view w2_setup_view -check_type setup -max_paths 50 \
    > "$output/reports/setup_timing.rpt"
  report_timing -view w2_setup_view -check_type recovery -max_paths 50 \
    > "$output/reports/recovery_timing.rpt"
  report_timing -view w2_setup_view -check_type clock_gating_setup -max_paths 50 \
    > "$output/reports/gating_setup_timing.rpt"
  report_timing -view w2_setup_view -check_type pulse_width -max_paths 50 \
    > "$output/reports/pulse_width_timing.rpt"
  set link_data_ports [get_ports link_data_o*]
  if {[sizeof_collection $link_data_ports] == 0} {
    error "canonical link_data_o ports are absent"
  }
  report_timing -view w2_setup_view -check_type setup \
    -to $link_data_ports -max_paths 50 \
    > "$output/reports/half_cycle_setup_timing.rpt"
  write_timing_machine_summary "$output/reports/setup_timing.machine" \
    w2_setup_view setup
  write_timing_machine_summary "$output/reports/recovery_timing.machine" \
    w2_setup_view recovery
  write_timing_machine_summary "$output/reports/gating_setup_timing.machine" \
    w2_setup_view clock_gating_setup
  write_timing_machine_summary "$output/reports/pulse_width_timing.machine" \
    w2_setup_view pulse_width
  write_timing_machine_summary "$output/reports/half_cycle_setup_timing.machine" \
    w2_setup_view setup half_cycle_setup $link_data_ports

  setAnalysisMode -checkType hold
  report_timing -view w2_hold_view -check_type hold -max_paths 50 \
    > "$output/reports/hold_timing.rpt"
  report_timing -view w2_hold_view -check_type removal -max_paths 50 \
    > "$output/reports/removal_timing.rpt"
  report_timing -view w2_hold_view -check_type clock_gating_hold -max_paths 50 \
    > "$output/reports/gating_hold_timing.rpt"
  report_timing -view w2_hold_view -check_type hold \
    -to $link_data_ports -max_paths 50 \
    > "$output/reports/half_cycle_hold_timing.rpt"
  write_timing_machine_summary "$output/reports/hold_timing.machine" \
    w2_hold_view hold
  write_timing_machine_summary "$output/reports/removal_timing.machine" \
    w2_hold_view removal
  write_timing_machine_summary "$output/reports/gating_hold_timing.machine" \
    w2_hold_view clock_gating_hold
  write_timing_machine_summary "$output/reports/half_cycle_hold_timing.machine" \
    w2_hold_view hold half_cycle_hold $link_data_ports
  setAnalysisMode -checkType setup
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
  write_sdf "$output/netlist/${top}.postroute.sdf"
  rcOut -spef "$output/netlist/${top}.postroute.spef"
  # The golden's Stylus write_db failed with IMPIMEX-7043 and explicitly
  # required saveDesign -mmmc2 for an MMMC1 design.
  saveDesign -mmmc2 "$output/database/${top}.enc"

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
