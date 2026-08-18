foreach name {SE_SETUP_LIB SE_HOLD_LIB SE_SHARED_QRC SE_MAPPED_SDC} {
  if {![info exists ::env($name)] || $::env($name) eq "" ||
      ![file isfile $::env($name)]} {
    error "missing required MMMC file $name"
  }
}

create_library_set -name se_setup_libset -timing [list $::env(SE_SETUP_LIB)]
create_library_set -name se_hold_libset -timing [list $::env(SE_HOLD_LIB)]
create_rc_corner -name se_setup_rc -qrc_tech $::env(SE_SHARED_QRC)
create_rc_corner -name se_hold_rc -qrc_tech $::env(SE_SHARED_QRC)
create_delay_corner -name se_setup_delay \
  -library_set se_setup_libset -rc_corner se_setup_rc
create_delay_corner -name se_hold_delay \
  -library_set se_hold_libset -rc_corner se_hold_rc
create_constraint_mode -name se_functional \
  -sdc_files [list $::env(SE_MAPPED_SDC)]
create_analysis_view -name se_setup_view \
  -constraint_mode se_functional -delay_corner se_setup_delay
create_analysis_view -name se_hold_view \
  -constraint_mode se_functional -delay_corner se_hold_delay
set_analysis_view -setup [list se_setup_view] -hold [list se_hold_view]
