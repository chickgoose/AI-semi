# Phase-4 only: syntax/elaboration compatibility smoke, not synthesis or PPA.
set TOP mc_wtb_occurrence_baseline_top
read_libs /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
read_hdl -sv [list \
  rtl/a2_batched_iwrr_k2.sv \
  rtl/mc_wtb_occurrence_baseline_top.sv]
elaborate $TOP
check_design -unresolved
puts "MC_WTB_OCCURRENCE_BASELINE_GENUS_ELABORATION_PASS"
exit
