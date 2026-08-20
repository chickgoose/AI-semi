# Syntax/elaboration compatibility smoke only; this is not mapped PPA evidence.
set TOP mc_wtb_epoch_route_interlock
set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_ROOT [file normalize [file join $SCRIPT_DIR ../..]]
read_libs /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
read_hdl -sv [list \
  [file join $REPO_ROOT rtl/candidates/mc_wtb_motion_qualification/mc_wtb_epoch_route_interlock.sv]]
elaborate $TOP
check_design -unresolved
puts "MC_WTB_EPOCH_ROUTE_INTERLOCK_GENUS_ELABORATION_PASS"
exit
