# Fail-closed setup/hold MMMC template for the P6 multi-clock SDC.

proc p6_mmmc_require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "P6 MMMC required environment variable is unset: $name"
  }
  return $::env($name)
}

proc p6_mmmc_require_file {label path} {
  if {![file exists $path] || ![file isfile $path] || [file size $path] == 0} {
    error "P6 MMMC $label is missing or empty: $path"
  }
  return $path
}

set p6_setup_lib [p6_mmmc_require_file setup_liberty \
  [p6_mmmc_require_env P6_SETUP_LIBERTY]]
set p6_hold_lib [p6_mmmc_require_file hold_liberty \
  [p6_mmmc_require_env P6_HOLD_LIBERTY]]
set p6_setup_qrc [p6_mmmc_require_file setup_qrc \
  [p6_mmmc_require_env P6_SETUP_QRC_TECH]]
set p6_hold_qrc [p6_mmmc_require_file hold_qrc \
  [p6_mmmc_require_env P6_HOLD_QRC_TECH]]
set p6_sdc [p6_mmmc_require_file multiclock_sdc \
  [p6_mmmc_require_env P6_MULTICLOCK_SDC]]
set p6_setup_temperature [p6_mmmc_require_env P6_SETUP_RC_TEMPERATURE_C]
set p6_hold_temperature [p6_mmmc_require_env P6_HOLD_RC_TEMPERATURE_C]

foreach pair [list \
  [list setup_rc_temperature $p6_setup_temperature] \
  [list hold_rc_temperature $p6_hold_temperature]] {
  lassign $pair label value
  if {![string is double -strict $value]} {
    error "P6 MMMC $label is not numeric: '$value'"
  }
}

if {[file normalize $p6_setup_lib] eq [file normalize $p6_hold_lib]} {
  error "P6 MMMC setup and hold Liberty files must be distinct"
}
if {[file normalize $p6_setup_qrc] eq [file normalize $p6_hold_qrc] &&
    $p6_setup_temperature == $p6_hold_temperature} {
  error "P6 MMMC setup and hold RC conditions are identical"
}

create_library_set -name p6_setup_libset -timing [list $p6_setup_lib]
create_library_set -name p6_hold_libset -timing [list $p6_hold_lib]
create_rc_corner -name p6_setup_rc -qrc_tech $p6_setup_qrc \
  -temperature $p6_setup_temperature
create_rc_corner -name p6_hold_rc -qrc_tech $p6_hold_qrc \
  -temperature $p6_hold_temperature
create_delay_corner -name p6_setup_corner \
  -library_set p6_setup_libset -rc_corner p6_setup_rc
create_delay_corner -name p6_hold_corner \
  -library_set p6_hold_libset -rc_corner p6_hold_rc
create_constraint_mode -name p6_functional \
  -sdc_files [list $p6_sdc]
create_analysis_view -name p6_setup_view \
  -constraint_mode p6_functional -delay_corner p6_setup_corner
create_analysis_view -name p6_hold_view \
  -constraint_mode p6_functional -delay_corner p6_hold_corner
set_analysis_view -setup [list p6_setup_view] -hold [list p6_hold_view]

puts "P6_MULTICLOCK_MMMC_READY setup_view=p6_setup_view hold_view=p6_hold_view"
