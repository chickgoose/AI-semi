# Slow-Liberty setup / fast-Liberty hold with one disclosed shared typical QRC.
proc w2_mmmc_file {name} {
  if {![info exists ::env($name)] || ![file isfile $::env($name)] || [file size $::env($name)] == 0} {
    error "missing/empty $name"
  }
  return $::env($name)
}
set setup_lib [w2_mmmc_file W2_SETUP_LIBERTY]
set hold_lib [w2_mmmc_file W2_HOLD_LIBERTY]
set shared_qrc [w2_mmmc_file W2_SHARED_TYPICAL_QRC]
set strict_sdc [w2_mmmc_file W2_STRICT_MULTICLOCK_SDC]
if {[file normalize $setup_lib] eq [file normalize $hold_lib]} {
  error "setup and hold Liberty must be distinct"
}
create_library_set -name w2_setup_libset -timing [list $setup_lib]
create_library_set -name w2_hold_libset -timing [list $hold_lib]
create_rc_corner -name w2_shared_setup_rc -qrc_tech $shared_qrc
create_rc_corner -name w2_shared_hold_rc -qrc_tech $shared_qrc
create_delay_corner -name w2_setup_corner -library_set w2_setup_libset -rc_corner w2_shared_setup_rc
create_delay_corner -name w2_hold_corner -library_set w2_hold_libset -rc_corner w2_shared_hold_rc
create_constraint_mode -name w2_strict_functional -sdc_files [list $strict_sdc]
create_analysis_view -name w2_setup_view -constraint_mode w2_strict_functional -delay_corner w2_setup_corner
create_analysis_view -name w2_hold_view -constraint_mode w2_strict_functional -delay_corner w2_hold_corner
set_analysis_view -setup [list w2_setup_view] -hold [list w2_hold_view]
puts "W2_MMMC_READY shared_typical_qrc=1 slow_setup_fast_hold=1"
