proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

# W2 requires independent max/setup and min/hold timing models.  Reusing one
# slow view for hold is diagnostic-only and is deliberately rejected here.
set setup_library [file normalize [require_env AER_SETUP_LIBRARY_FILE]]
set hold_library  [file normalize [require_env AER_HOLD_LIBRARY_FILE]]
set setup_qrc     [file normalize [require_env AER_SETUP_QRC_TECH]]
set hold_qrc      [file normalize [require_env AER_HOLD_QRC_TECH]]
set sdc           [file normalize [require_env AER_PNR_SDC]]

if {$setup_library eq $hold_library} {
  error "setup and hold Liberty files must be distinct physical-corner inputs"
}
if {$setup_qrc eq $hold_qrc} {
  error "setup and hold QRC files must be distinct physical-corner inputs"
}

create_library_set -name w2_lib_setup -timing [list $setup_library]
create_library_set -name w2_lib_hold  -timing [list $hold_library]
create_rc_corner -name w2_rc_setup -qrc_tech $setup_qrc
create_rc_corner -name w2_rc_hold  -qrc_tech $hold_qrc
create_delay_corner -name w2_delay_setup \
  -library_set w2_lib_setup -rc_corner w2_rc_setup
create_delay_corner -name w2_delay_hold \
  -library_set w2_lib_hold -rc_corner w2_rc_hold
create_constraint_mode -name w2_constraints -sdc_files [list $sdc]
create_analysis_view -name w2_view_setup \
  -constraint_mode w2_constraints -delay_corner w2_delay_setup
create_analysis_view -name w2_view_hold \
  -constraint_mode w2_constraints -delay_corner w2_delay_hold
set_analysis_view -setup [list w2_view_setup] -hold [list w2_view_hold]
