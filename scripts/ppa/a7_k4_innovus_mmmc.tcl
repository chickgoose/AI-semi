proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

set library [require_env AER_LIBRARY_FILE]
set qrc     [require_env AER_QRC_TECH]
set sdc     [require_env AER_PNR_SDC]

create_library_set -name libset_slow -timing [list $library]
create_rc_corner -name rc_typical -qrc_tech $qrc
create_delay_corner -name delay_slow \
  -library_set libset_slow -rc_corner rc_typical
create_constraint_mode -name constraints_default -sdc_files [list $sdc]
create_analysis_view -name view_slow \
  -constraint_mode constraints_default -delay_corner delay_slow
set_analysis_view -setup {view_slow} -hold {view_slow}
