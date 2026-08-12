foreach required {W7_DESIGN W7_OUT W7_MAPPED_NETLIST W7_MMMC W7_TECH_LEF W7_MACRO_LEF} {
  if {![info exists ::env($required)]} { error "$required is required" }
}

set design $::env(W7_DESIGN)
set out $::env(W7_OUT)
file mkdir $out

set init_top_cell $design
set init_verilog [list $::env(W7_MAPPED_NETLIST)]
set init_lef_file [list $::env(W7_TECH_LEF) $::env(W7_MACRO_LEF)]
set init_mmmc_file $::env(W7_MMMC)
set init_pwr_net VDD
set init_gnd_net VSS
init_design

setDesignMode -process 45
setAnalysisMode -analysisType onChipVariation -cppr both
setPlaceMode -place_global_ignore_scan true
set_analysis_view -setup [list setup_view] -hold [list hold_view]

set site_names [dbGet head.sites.name]
puts "W7_SITE_QUERY=$site_names"
foreach required_site {CoreSite CoreSiteDouble} {
  if {[lsearch -exact $site_names $required_site] < 0} {
    error "$required_site missing from queried sites"
  }
}

# CoreSite is the base row grid.  Add an overlapping double-height row grid so
# every library cell site used by the mapped netlist has legal placement rows.
floorPlan -site CoreSite -r 1.0 0.55 12 12 12 12
set core_box [dbGet top.fPlan.coreBox]
createRow -site CoreSiteDouble -area $core_box
set used_sites [lsort -unique [dbGet top.insts.cell.site.name]]
puts "W7_USED_SITES=$used_sites"
foreach used_site $used_sites {
  if {$used_site ne "" && [lsearch -exact {CoreSite CoreSiteDouble} $used_site] < 0} {
    error "used instance site has no legal row plan: $used_site"
  }
}

globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
set all_io [dbGet top.terms.name]
if {[llength $all_io] == 0} { error "design has no IO ports" }
editPin -pin $all_io -side Left -layer Metal3 -spreadType side

addRing -nets {VDD VSS} -type core_rings -follow core \
  -layer {top Metal4 bottom Metal4 left Metal5 right Metal5} \
  -width 1.0 -spacing 0.5 -offset 1.0
puts "W7_PG_FOLLOWPIN=sroute_corePin"
sroute -connect {corePin} -nets {VDD VSS} -allowJogging 1 \
  -allowLayerChange 1 -corePinTarget {firstAfterRowEnd}

place_opt_design
proc w7_db_count {items} {
  set count 0
  foreach item $items { if {$item ne "" && $item ne "0x0"} { incr count } }
  return $count
}
set unplaced_insts [dbGet top.insts.pStatus unplaced -p]
set unplaced_ports [dbGet top.terms.pStatus unplaced -p]
set unplaced_inst_count [w7_db_count $unplaced_insts]
set unplaced_port_count [w7_db_count $unplaced_ports]
puts "W7_UNPLACED_INSTS=$unplaced_inst_count"
puts "W7_UNPLACED_PORTS=$unplaced_port_count"
if {$unplaced_inst_count != 0} { error "unplaced instances remain: $unplaced_insts" }
if {$unplaced_port_count != 0} { error "unplaced IO ports remain: $unplaced_ports" }

clock_opt_design
routeDesign
sroute -connect {corePin} -nets {VDD VSS} -allowJogging 1 \
  -allowLayerChange 1 -corePinTarget {firstAfterRowEnd}
extractRC
optDesign -postRoute
optDesign -postRoute -hold
extractRC

timeDesign -postRoute -pathReports -drvReports -slackReports -numPaths 50 \
  -outDir $out/timing_setup
timeDesign -postRoute -hold -pathReports -slackReports -numPaths 50 \
  -outDir $out/timing_hold
set_analysis_view -setup [list setup_view] -hold [list hold_view]
# Innovus 23.14 selects the setup/hold analysis from -check_type when both
# views are active.  Combining -check_type with -late/-early is illegal.
puts "W7_RECOVERY_ANALYSIS_VIEW=setup_view"
puts "W7_REMOVAL_ANALYSIS_VIEW=hold_view"
report_timing -check_type recovery -max_paths 50 > $out/timing_recovery.rpt
report_timing -check_type removal -max_paths 50 > $out/timing_removal.rpt

proc w7_timing_metric {channel label switches} {
  set paths [eval report_timing $switches -max_paths 10000 -collection]
  set path_count [sizeof_collection $paths]
  set violations 0
  set wns 0.0
  set tns 0.0
  set initialized 0
  if {$path_count > 0} {
    foreach slack [get_db $paths .slack] {
      if {!$initialized || $slack < $wns} { set wns $slack; set initialized 1 }
      if {$slack < 0.0} { incr violations; set tns [expr {$tns + $slack}] }
    }
  }
  puts $channel [format \
    "W7_TIMING_METRIC check=%s paths=%d violations=%d wns=%.6f tns=%.6f" \
    $label $path_count $violations $wns $tns]
}
set metric_file [open $out/timing_metrics.rpt w]
w7_timing_metric $metric_file setup {-late}
w7_timing_metric $metric_file hold {-early}
w7_timing_metric $metric_file recovery {-check_type recovery}
w7_timing_metric $metric_file removal {-check_type removal}
w7_timing_metric $metric_file reset_ref_recovery \
  {-check_type recovery -from [get_ports rst_n] -to [all_registers -clock ref_clk]}
w7_timing_metric $metric_file reset_ref_removal \
  {-check_type removal -from [get_ports rst_n] -to [all_registers -clock ref_clk]}
set icg_latch_pins [get_pins -hierarchical *clock_boundary*enable_latched_q_reg*/*]
if {[sizeof_collection $icg_latch_pins] == 0} {
  error "no mapped ICG enable-latch pins found for reset/sample timing coverage"
}
w7_timing_metric $metric_file reset_sample_setup \
  [list -late -from [get_ports rst_n] -to $icg_latch_pins]
w7_timing_metric $metric_file reset_sample_hold \
  [list -early -from [get_ports rst_n] -to $icg_latch_pins]
if {$design eq "a7_weighted_fovea_ddr"} {
  set link_clock ddr_link_clk
} else {
  set link_clock parallel_link_clk
}
w7_timing_metric $metric_file reset_link_recovery \
  [list -check_type recovery -from [get_ports rst_n] -to [all_registers -clock $link_clock]]
w7_timing_metric $metric_file reset_link_removal \
  [list -check_type removal -from [get_ports rst_n] -to [all_registers -clock $link_clock]]
close $metric_file

set unconstrained_paths [report_timing -unconstrained -max_paths 10000 -collection]
puts "W7_UNCONSTRAINED_PATHS=[sizeof_collection $unconstrained_paths]"
report_area > $out/area_postroute.rpt
report_power > $out/power_vectorless_postroute.rpt
report_clock_timing -type summary > $out/clock_timing.rpt
check_timing -verbose > $out/check_timing.rpt
checkPlace $out/check_place.rpt
verify_drc -report $out/drc.rpt
verifyConnectivity -type all -report $out/connectivity.rpt
saveDesign $out/${design}_postroute.enc
saveNetlist $out/${design}_postroute.v
write_sdf $out/${design}_postroute.sdf

foreach required [list \
    $out/check_timing.rpt $out/check_place.rpt $out/drc.rpt \
    $out/connectivity.rpt $out/timing_recovery.rpt $out/timing_removal.rpt \
    $out/timing_metrics.rpt \
    $out/${design}_postroute.v $out/${design}_postroute.sdf] {
  if {![file exists $required] || [file size $required] == 0} {
    error "empty required Innovus artifact: $required"
  }
}
foreach timing_dir [list $out/timing_setup $out/timing_hold] {
  if {![file isdirectory $timing_dir] || [llength [glob -nocomplain $timing_dir/*]] == 0} {
    error "empty required timing directory: $timing_dir"
  }
}

set marker [open $out/W7_INNOVUS_CLEAN_END w]
puts $marker "W7_INNOVUS_CLEAN_END design=$design"
close $marker
puts "W7_INNOVUS_CLEAN_END design=$design"
exit
