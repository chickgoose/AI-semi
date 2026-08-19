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

proc se_timing_metrics {view check} {
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
  return [list $path_count $violations $wns $tns]
}

proc se_timing_summary {path view check} {
  lassign [se_timing_metrics $view $check] path_count violations wns tns
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

proc se_timing_improved {before after} {
  lassign $before before_paths before_violations before_wns before_tns
  lassign $after after_paths after_violations after_wns after_tns
  set epsilon 0.000001
  return [expr {$after_violations < $before_violations ||
    ($after_violations == $before_violations &&
      ($after_wns > $before_wns + $epsilon ||
       (abs($after_wns - $before_wns) <= $epsilon &&
        $after_tns > $before_tns + $epsilon)))}]
}

proc se_write_eco_receipt {path phase view check optimizer allow_setup_degrade \
                           max_iterations status rows} {
  set handle [open $path {WRONLY CREAT EXCL}]
  puts $handle "schema=k2_single_edge_eco_iteration_receipt_v1"
  puts $handle "phase=$phase"
  puts $handle "view=$view"
  puts $handle "check=$check"
  puts $handle "optimizer=$optimizer"
  puts $handle "allow_setup_tns_degrade=$allow_setup_degrade"
  puts $handle "max_iterations=$max_iterations"
  puts $handle "status=$status"
  puts $handle "observation_count=[llength $rows]"
  set index 0
  foreach metrics $rows {
    lassign $metrics path_count violation_count wns tns
    puts $handle "observation_${index}=$path_count,$violation_count,$wns,$tns"
    incr index
  }
  close $handle
}

proc se_run_eco_phase {path phase view check optimizer allow_setup_degrade \
                       max_iterations} {
  set rows [list [se_timing_metrics $view $check]]
  set status CLOSED
  for {set iteration 1} {$iteration <= $max_iterations} {incr iteration} {
    set before [lindex $rows end]
    if {[lindex $before 1] == 0} { break }
    if {$optimizer eq "postRoute_hold"} {
      optDesign -postRoute -hold
    } elseif {$optimizer eq "postRoute"} {
      optDesign -postRoute
    } else {
      error "unsupported ECO optimizer $optimizer"
    }
    extractRC
    set after [se_timing_metrics $view $check]
    lappend rows $after
    if {![se_timing_improved $before $after]} {
      set status STALLED
      break
    }
  }
  if {[lindex [lindex $rows end] 1] != 0 && $status eq "CLOSED"} {
    set status EXHAUSTED
  }
  se_write_eco_receipt $path $phase $view $check $optimizer \
    $allow_setup_degrade $max_iterations $status $rows
  return $status
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
  # MMMC can return one se_primary_clk object per active analysis view. Count
  # unique clock names rather than raw view-scoped objects, while still
  # rejecting a second/generated clock name or a missing/duplicated clk_i port.
  set clock_names [lsort -unique [get_object_name [get_clocks *]]]
  if {[sizeof_collection $clock_ports] != 1 ||
      [llength $clock_names] != 1 || [lindex $clock_names 0] ne "se_primary_clk"} {
    error "initialized endpoint does not have exactly one primary clock"
  }
  set_drive 0 $clock_ports
  setDesignMode -process $::env(SE_PROCESS)
  setAnalysisMode -analysisType onChipVariation -cppr both

  # The pinned GPDK045 LEF assigns BUFX2 to CoreSiteDouble even though the
  # macro is only one CoreSite high.  A CoreSite-only floorplan therefore
  # cannot legally place it.  Normalize mapped instances to the same-site
  # BUFX4 before floorplanning and keep optimization from reintroducing BUFX2.
  # This matches the repository's previously exercised core physical flow.
  set bufx2_cells [get_db base_cells -if {.name == BUFX2}]
  set bufx4_cells [get_db base_cells -if {.name == BUFX4}]
  if {[llength $bufx2_cells] != 1 || [llength $bufx4_cells] != 1} {
    error "required BUFX2/BUFX4 cells are not uniquely available"
  }
  if {[get_db [lindex $bufx4_cells 0] .site.name] ne $::env(SE_SITE)} {
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
  if {[llength $rows] == 0} { error "floorplan created no rows" }
  foreach row_site [dbGet top.fPlan.rows.site.name -u] {
    if {$row_site ne $::env(SE_SITE)} { error "non-contract placement site $row_site" }
  }
  foreach instance_site [lsort -unique [get_db insts .base_cell.site.name]] {
    if {$instance_site ne $::env(SE_SITE)} {
      error "mapped instance uses non-contract site $instance_site"
    }
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
  clock_opt_design
  # Build/trim the special PG network before signal routing so NanoRoute can
  # legally avoid those shapes.  Adding special M1 wires after routeDesign can
  # create signal-to-VDD/VSS shorts that post-route optimization cannot repair.
  sroute -nets [list $::env(SE_VDD) $::env(SE_VSS)] \
    -connect {blockPin padPin corePin}
  editTrim -nets [list $::env(SE_VDD) $::env(SE_VSS)]
  routeDesign
  extractRC
  optDesign -postRoute
  setOptMode -fixHoldAllowSetupTnsDegrade true
  optDesign -postRoute -hold
  extractRC

  # Server-proven bounded sequence: three hold passes, six setup-recovery
  # passes, then three final hold passes. Each phase stops on closure,
  # monotonic stall, or exhaustion and emits an exclusive machine receipt.
  set eco_phase_failures {}
  setOptMode -fixHoldAllowSetupTnsDegrade true
  setAnalysisMode -checkType hold
  set pre_hold_status [se_run_eco_phase \
    "$output/reports/eco_hold_pre_setup.machine" pre_setup_hold \
    se_hold_view hold postRoute_hold true 3]
  if {$pre_hold_status ne "CLOSED"} {
    lappend eco_phase_failures "pre-setup hold ECO: $pre_hold_status"
  }

  setAnalysisMode -checkType setup
  set setup_status [se_run_eco_phase \
    "$output/reports/eco_setup_recovery.machine" setup_recovery \
    se_setup_view setup postRoute NA 6]
  if {$setup_status ne "CLOSED"} {
    lappend eco_phase_failures "setup recovery ECO: $setup_status"
  }

  # A 5 ps positive target prevents Innovus from treating sub-picosecond
  # negative hold residue as already close enough. Final setup is still
  # independently re-measured and must close after this phase.
  setOptMode -fixHoldAllowSetupTnsDegrade true
  setOptMode -opt_hold_target_slack 0.005
  setAnalysisMode -checkType hold
  set final_hold_status [se_run_eco_phase \
    "$output/reports/eco_hold_final.machine" final_hold_reclosure \
    se_hold_view hold postRoute_hold true 3]
  if {$final_hold_status ne "CLOSED"} {
    lappend eco_phase_failures "final hold ECO: $final_hold_status"
  }

  # Check the final optimized placement, including cells inserted by CTS and
  # post-route hold repair, rather than only the pre-CTS placement snapshot.
  foreach instance_site [lsort -unique [get_db insts .base_cell.site.name]] {
    if {$instance_site ne $::env(SE_SITE)} {
      error "post-route instance uses non-contract site $instance_site"
    }
  }
  redirect -file "$output/reports/check_place.rpt" {checkPlace}

  # Preserve all independently safe post-route diagnostics even when either
  # timing view is not closed.  The stage still exits nonzero after collection.
  set diagnostic_failures {}
  set setup_failed [catch {
    setAnalysisMode -checkType setup
    report_timing -view se_setup_view -check_type setup -max_paths 50 \
      > "$output/reports/setup_timing.rpt"
    se_append_report_context "$output/reports/setup_timing.rpt" setup_timing postroute
    se_timing_summary "$output/reports/setup_timing.machine" se_setup_view setup
  } setup_error]
  if {$setup_failed} { lappend diagnostic_failures "setup: $setup_error" }
  set hold_failed [catch {
    setAnalysisMode -checkType hold
    report_timing -view se_hold_view -check_type hold -max_paths 50 \
      > "$output/reports/hold_timing.rpt"
    se_append_report_context "$output/reports/hold_timing.rpt" hold_timing postroute
    se_timing_summary "$output/reports/hold_timing.machine" se_hold_view hold
  } hold_error]
  if {$hold_failed} { lappend diagnostic_failures "hold: $hold_error" }
  setAnalysisMode -checkType setup

  report_area > "$output/reports/area.rpt"
  se_append_report_context "$output/reports/area.rpt" area postroute
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
  if {[llength $diagnostic_failures] != 0} {
    error "timing diagnostics failed after complete safe report collection: [join $diagnostic_failures {; }]"
  }
  if {[llength $eco_phase_failures] != 0} {
    error "bounded ECO phases failed after complete safe report collection: [join $eco_phase_failures {; }]"
  }
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
