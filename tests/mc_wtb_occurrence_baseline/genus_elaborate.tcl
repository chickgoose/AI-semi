# Phase-4 only: syntax/elaboration compatibility smoke, not synthesis or PPA.
set TOP mc_wtb_occurrence_baseline_top
set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_ROOT [file normalize [file join $SCRIPT_DIR ../..]]
read_libs /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
read_hdl -sv [list \
  [file join $REPO_ROOT rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv] \
  [file join $REPO_ROOT rtl/candidates/mc_wtb_occurrence_baseline/mc_wtb_occurrence_baseline_top.sv]]
elaborate $TOP
check_design -unresolved
puts "MC_WTB_OCCURRENCE_BASELINE_GENUS_ELABORATION_PASS"
exit
