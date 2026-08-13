foreach name {CORE_SETUP_LIB CORE_HOLD_LIB CORE_SHARED_QRC CORE_MAPPED_SDC} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "missing required MMMC environment variable $name"
  }
  if {![file isfile $::env($name)]} {
    error "required MMMC input is not a regular file: $::env($name)"
  }
}

create_library_set -name core_setup_libset -timing [list $::env(CORE_SETUP_LIB)]
create_library_set -name core_hold_libset -timing [list $::env(CORE_HOLD_LIB)]
create_rc_corner -name core_setup_rc -qrc_tech $::env(CORE_SHARED_QRC)
create_rc_corner -name core_hold_rc -qrc_tech $::env(CORE_SHARED_QRC)
create_delay_corner -name core_setup_delay \
  -library_set core_setup_libset -rc_corner core_setup_rc
create_delay_corner -name core_hold_delay \
  -library_set core_hold_libset -rc_corner core_hold_rc
create_constraint_mode -name core_functional \
  -sdc_files [list $::env(CORE_MAPPED_SDC)]
create_analysis_view -name core_setup_view \
  -constraint_mode core_functional -delay_corner core_setup_delay
create_analysis_view -name core_hold_view \
  -constraint_mode core_functional -delay_corner core_hold_delay
set_analysis_view -setup [list core_setup_view] -hold [list core_hold_view]
