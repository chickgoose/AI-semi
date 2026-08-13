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

proc timing_metrics {view check} {
  set paths [report_timing -collection -view $view -check_type $check \
    -max_paths 1000000]
  set path_count [sizeof_collection $paths]
  if {$path_count == 0} { error "no $check timing paths in $view" }
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
  return [list $path_count $violation_count $wns $tns]
}

proc hold_metrics_improved {before after} {
  lassign $before before_paths before_violations before_wns before_tns
  lassign $after after_paths after_violations after_wns after_tns
  set epsilon 0.000001
  return [expr {$after_violations < $before_violations ||
    ($after_violations == $before_violations &&
      ($after_wns > $before_wns + $epsilon ||
       (abs($after_wns - $before_wns) <= $epsilon &&
        $after_tns > $before_tns + $epsilon)))}]
}

proc setup_metrics_improved {before after} {
  lassign $before before_paths before_violations before_wns before_tns
  lassign $after after_paths after_violations after_wns after_tns
  set epsilon 0.000001
  return [expr {$after_violations < $before_violations ||
    ($after_violations == $before_violations &&
      ($after_wns > $before_wns + $epsilon ||
       (abs($after_wns - $before_wns) <= $epsilon &&
        $after_tns > $before_tns + $epsilon)))}]
}

proc write_hold_closure {path phase policy status rows} {
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_w2_hold_closure_v1"
  puts $handle "phase=$phase"
  puts $handle "check=hold"
  puts $handle "view=w2_hold_view"
  puts $handle "optimizer=postRoute_hold"
  puts $handle "allow_setup_tns_degrade=$policy"
  puts $handle "status=$status"
  puts $handle "max_iterations=3"
  puts $handle "observation_count=[llength $rows]"
  set index 0
  foreach metrics $rows {
    lassign $metrics path_count violation_count wns tns
    puts $handle "observation_${index}=$path_count,$violation_count,$wns,$tns"
    incr index
  }
  close $handle
}

proc write_setup_closure {path status rows} {
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_w2_setup_closure_v1"
  puts $handle "phase=setup_recovery"
  puts $handle "check=setup"
  puts $handle "view=w2_setup_view"
  puts $handle "optimizer=postRoute"
  puts $handle "allow_setup_tns_degrade=NA"
  puts $handle "status=$status"
  puts $handle "max_iterations=3"
  puts $handle "observation_count=[llength $rows]"
  set index 0
  foreach metrics $rows {
    lassign $metrics path_count violation_count wns tns
    puts $handle "observation_${index}=$path_count,$violation_count,$wns,$tns"
    incr index
  }
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
set timing_profile [require_env AER_W2_TIMING_PROFILE]
set timing_profile_sha256 [require_env AER_W2_TIMING_PROFILE_SHA256]
set timing_period [require_env AER_W2_PERIOD_NS]
set hold_setup_degrade [require_env AER_HOLD_FIX_ALLOW_SETUP_TNS_DEGRADE]

if {$timing_profile eq "three_endpoint_5p0ns"} {
  if {$timing_period ne "5.0" || $hold_setup_degrade ne "false"} {
    error "5.0ns timing profile/hold policy mismatch"
  }
} elseif {$timing_profile eq "three_endpoint_5p7ns"} {
  if {$timing_period ne "5.7" || $hold_setup_degrade ne "true"} {
    error "5.7ns timing profile/hold policy mismatch"
  }
} elseif {$timing_profile eq "three_endpoint_6p5ns"} {
  if {$timing_period ne "6.5" || $hold_setup_degrade ne "true"} {
    error "6.5ns timing profile/hold policy mismatch"
  }
} else {
  error "unsupported W2 timing profile $timing_profile"
}
if {![regexp {^[0-9a-f]{64}$} $timing_profile_sha256]} {
  error "timing profile SHA-256 is malformed"
}

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
  # Select the one producer-aligned constraint mode before any post-init
  # boundary updates; Innovus 23.14 otherwise rejects them with TCLCMD-1048.
  set_interactive_constraint_modes [list w2_strict_functional]

  set boundary_clock_ports [get_ports {ref_clk_i sample_clk_i}]
  if {[sizeof_collection $boundary_clock_ports] != 2} {
    error "expected exactly ref_clk_i and sample_clk_i clock ports"
  }
  set boundary_nonclock_inputs [remove_from_collection \
    [all_inputs] $boundary_clock_ports]
  set expected_boundary_nonclock_inputs [get_ports {rst_n source_pending_i*}]
  if {[sizeof_collection $expected_boundary_nonclock_inputs] != 17 ||
      [sizeof_collection [remove_from_collection $boundary_nonclock_inputs \
        $expected_boundary_nonclock_inputs]] != 0 ||
      [sizeof_collection [remove_from_collection $expected_boundary_nonclock_inputs \
        $boundary_nonclock_inputs]] != 0} {
    error "canonical nonclock inputs are not exactly rst_n plus source_pending_i[15:0]"
  }
  set_drive 0 $boundary_clock_ports
  set_driving_cell -lib_cell BUFX2 $boundary_nonclock_inputs

  # The mapped SDC ends its generated clock at the preserved endpoint ICG ECK.
  # Materialize the actual forwarded boundary clock from that exact pin onto
  # link_clk_o; checking for a pre-existing port clock is insufficient.
  set forwarded_link_source [get_pins -hierarchical *w2_ep_icg_0/ECK]
  set forwarded_link_port [get_ports link_clk_o]
  if {[sizeof_collection $forwarded_link_source] != 1 ||
      [sizeof_collection $forwarded_link_port] != 1} {
    error "expected exactly one *w2_ep_icg_0/ECK source and link_clk_o target"
  }
  create_generated_clock -name w2_forwarded_link_port_clk \
    -source $forwarded_link_source -divide_by 1 $forwarded_link_port
  set boundary_handle [open \
    "$output/reports/boundary_timing.machine" {WRONLY CREAT EXCL}]
  puts $boundary_handle "schema=k2_w2_boundary_timing_v1"
  puts $boundary_handle "timing_profile=$timing_profile"
  puts $boundary_handle "timing_profile_sha256=$timing_profile_sha256"
  puts $boundary_handle "period_ns=$timing_period"
  puts $boundary_handle "clock_ports=ref_clk_i,sample_clk_i"
  puts $boundary_handle "clock_drive=0"
  puts $boundary_handle "nonclock_input_ports=rst_n,source_pending_i"
  puts $boundary_handle "nonclock_driving_cell=BUFX2"
  puts $boundary_handle "forwarded_link_clock=*w2_ep_icg_0/ECK,link_clk_o,divide_by_1"
  puts $boundary_handle "link_clock_false_path=FORBIDDEN"
  puts $boundary_handle "hold_fix_allow_setup_tns_degrade=$hold_setup_degrade"
  close $boundary_handle
  setDesignMode -process $process

  # OCV is common to both candidates and CPPR is enabled symmetrically.
  setAnalysisMode -analysisType onChipVariation -cppr both
  # Bind the selected profile's hold policy before every hold optimization,
  # including the first post-route hold pass below.
  setOptMode -fixHoldAllowSetupTnsDegrade $hold_setup_degrade

  # BUFX2 is anomalously declared on CoreSiteDouble while its physical height
  # is one CoreSite row.  Creating overlapping rows for that site caused the
  # observed post-place overlaps, 93% density, Metal1 shorts, and PG opens.
  # Canonicalize any mapped occurrence, then prevent optimization from adding
  # the cell again.  BUFX4 is the common single-row functional replacement.
  set bufx2_base_cells [get_db base_cells -if {.name == BUFX2}]
  set bufx4_base_cells [get_db base_cells -if {.name == BUFX4}]
  if {[llength $bufx2_base_cells] != 1 || [llength $bufx4_base_cells] != 1} {
    error "required BUFX2/BUFX4 library cells are not uniquely available"
  }
  if {[get_db [lindex $bufx4_base_cells 0] .site.name] ne $site} {
    error "BUFX4 replacement does not use canonical site $site"
  }
  foreach bufx2_inst [get_db insts -if {.base_cell.name == BUFX2}] {
    ecoChangeCell -inst [get_db $bufx2_inst .name] -cell BUFX4
  }
  if {[llength [get_db insts -if {.base_cell.name == BUFX2}]] != 0} {
    error "BUFX2 instances remain after canonical-site replacement"
  }
  setDontUse BUFX2 true

  # An explicit library site is mandatory.  The old implicit floorPlan call
  # could create no legal rows or choose a different site across bundles.
  # The server golden proves the -r form on Innovus 23.14.  Freeze the actual
  # CoreSite result by inspecting the rows immediately afterward instead of
  # relying on an unproven floorPlan option spelling.
  floorPlan -r $aspect $util $margin $margin $margin $margin
  set core_box [get_db current_design .core_bbox]
  set used_sites [lsort -unique [get_db insts .base_cell.site.name]]
  foreach used_site $used_sites {
    if {$used_site ne $site} {
      error "mapped instance uses unsupported placement site $used_site"
    }
  }
  set row_names [dbGet top.fPlan.rows.name]
  if {[llength $row_names] == 0} {
    error "floorplan created no standard-cell rows for site $site"
  }
  set actual_row_sites [dbGet top.fPlan.rows.site.name -u]
  if {[lsearch -exact $actual_row_sites $site] < 0} {
    error "floorplan is missing required placement rows for site $site"
  }
  foreach row_site $actual_row_sites {
    if {$row_site ne $site} {
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
  # Connect PG only after every optimization/CTS insertion and movement is
  # complete.  The previous pre-place sroute left final cells disconnected
  # and placed cells across stale Metal1 special wires.
  sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}
  # Innovus 23.14 sroute can leave zero-length Metal1 stubs after otherwise
  # complete core-pin routing.  Trim only dangling special-wire branches on
  # the two PG nets before extraction and fail-closed connectivity checks.
  editTrim -nets [list $vdd $vss]
  # Materialize any signal-versus-PG DRC markers introduced by final sroute,
  # then let NanoRoute repair only those marked shapes.  Innovus 23.14
  # the targeted repair reverts its snapshot if the violation count increases;
  # the independent final DRC/connectivity reports remain authoritative.
  verify_drc -report "$output/reports/drc_pre_signal_eco.rpt"
  ecoRoute -fix_drc
  extractRC
  # Innovus 23.14 rejects interactive constraint updates in an MMMC design
  # until their constraint mode is selected explicitly (TCLCMD-1048).  Both
  # setup and hold views intentionally share this one strict functional mode.
  set_propagated_clock [all_clocks]
  # The link uses a clock-selected serializer, so a clock-wide gating query
  # falsely classifies MX2 S0 and ordinary synthesized logic as clock gates.
  # Rank only the one preserved endpoint ICG enable constrained by the SDC.
  set endpoint_icg_enable [get_pins -hierarchical *w2_ep_icg_0/E]
  if {[sizeof_collection $endpoint_icg_enable] != 1} {
    error "expected exactly one preserved endpoint ICG enable pin"
  }

  # Hold ECOs performed before final PG routing left short conventional hold
  # paths unresolved.  Re-optimize after final PG/RC, reconnect any inserted
  # cells, and require strict monotonic progress until every hold path closes.
  setAnalysisMode -checkType hold
  set hold_closure_rows [list [timing_metrics w2_hold_view hold]]
  set hold_status CLOSED
  for {set hold_iteration 1} {$hold_iteration <= 3} {incr hold_iteration} {
    set before [lindex $hold_closure_rows end]
    if {[lindex $before 1] == 0} { break }
    optDesign -postRoute -hold
    sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}
    editTrim -nets [list $vdd $vss]
    extractRC
    set after [timing_metrics w2_hold_view hold]
    lappend hold_closure_rows $after
    if {![hold_metrics_improved $before $after]} {
      set hold_status STALLED
      break
    }
  }
  if {[lindex [lindex $hold_closure_rows end] 1] != 0 && $hold_status eq "CLOSED"} {
    set hold_status EXHAUSTED
  }
  write_hold_closure "$output/reports/hold_closure_pre_setup.machine" \
    pre_setup_hold $hold_setup_degrade $hold_status $hold_closure_rows
  if {$hold_status ne "CLOSED"} {
    error "pre-setup post-route hold closure did not converge: $hold_status"
  }

  # Hold ECOs are allowed to spend setup TNS for the 5.7/6.5 profiles.  The
  # real server runs showed that this can leave small negative setup slack even
  # after hold is clean.  Recover setup with bounded monotonic post-route
  # optimization, reconnect/extract every inserted cell, then perform a final
  # hold closure with setup degradation disabled.  The final setup/hold reports
  # below remain the authoritative signoff gate.
  setAnalysisMode -checkType setup
  set setup_closure_rows [list [timing_metrics w2_setup_view setup]]
  set setup_status CLOSED
  for {set setup_iteration 1} {$setup_iteration <= 3} {incr setup_iteration} {
    set before [lindex $setup_closure_rows end]
    if {[lindex $before 1] == 0} { break }
    optDesign -postRoute
    sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}
    editTrim -nets [list $vdd $vss]
    extractRC
    set after [timing_metrics w2_setup_view setup]
    lappend setup_closure_rows $after
    if {![setup_metrics_improved $before $after]} {
      set setup_status STALLED
      break
    }
  }
  if {[lindex [lindex $setup_closure_rows end] 1] != 0 && $setup_status eq "CLOSED"} {
    set setup_status EXHAUSTED
  }
  write_setup_closure "$output/reports/setup_closure.machine" \
    $setup_status $setup_closure_rows
  if {$setup_status ne "CLOSED"} {
    error "post-route setup recovery did not converge: $setup_status"
  }

  setOptMode -fixHoldAllowSetupTnsDegrade false
  setAnalysisMode -checkType hold
  set final_hold_rows [list [timing_metrics w2_hold_view hold]]
  set final_hold_status CLOSED
  for {set final_hold_iteration 1} {$final_hold_iteration <= 3} \
      {incr final_hold_iteration} {
    set before [lindex $final_hold_rows end]
    if {[lindex $before 1] == 0} { break }
    optDesign -postRoute -hold
    sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}
    editTrim -nets [list $vdd $vss]
    extractRC
    set after [timing_metrics w2_hold_view hold]
    lappend final_hold_rows $after
    if {![hold_metrics_improved $before $after]} {
      set final_hold_status STALLED
      break
    }
  }
  if {[lindex [lindex $final_hold_rows end] 1] != 0 && $final_hold_status eq "CLOSED"} {
    set final_hold_status EXHAUSTED
  }
  write_hold_closure "$output/reports/hold_closure.machine" \
    final_hold_reclosure false $final_hold_status $final_hold_rows
  if {$final_hold_status ne "CLOSED"} {
    error "final post-setup hold closure did not converge: $final_hold_status"
  }
  setAnalysisMode -checkType setup
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
  report_timing -view w2_setup_view -check_type clock_gating_setup \
    -to $endpoint_icg_enable -max_paths 50 \
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
    w2_setup_view clock_gating_setup gating_setup $endpoint_icg_enable
  write_timing_machine_summary "$output/reports/pulse_width_timing.machine" \
    w2_setup_view pulse_width
  write_timing_machine_summary "$output/reports/half_cycle_setup_timing.machine" \
    w2_setup_view setup half_cycle_setup $link_data_ports

  setAnalysisMode -checkType hold
  report_timing -view w2_hold_view -check_type hold -max_paths 50 \
    > "$output/reports/hold_timing.rpt"
  report_timing -view w2_hold_view -check_type removal -max_paths 50 \
    > "$output/reports/removal_timing.rpt"
  report_timing -view w2_hold_view -check_type clock_gating_hold \
    -to $endpoint_icg_enable -max_paths 50 \
    > "$output/reports/gating_hold_timing.rpt"
  report_timing -view w2_hold_view -check_type hold \
    -to $link_data_ports -max_paths 50 \
    > "$output/reports/half_cycle_hold_timing.rpt"
  write_timing_machine_summary "$output/reports/hold_timing.machine" \
    w2_hold_view hold
  write_timing_machine_summary "$output/reports/removal_timing.machine" \
    w2_hold_view removal
  write_timing_machine_summary "$output/reports/gating_hold_timing.machine" \
    w2_hold_view clock_gating_hold gating_hold $endpoint_icg_enable
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
