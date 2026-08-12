if {![info exists ::env(W7_LIB)] || ![info exists ::env(W7_QRC)] ||
    ![info exists ::env(W7_MAPPED_SDC)]} {
  error "W7_LIB, W7_QRC, and W7_MAPPED_SDC are required"
}

create_library_set -name slow_lib -timing [list $::env(W7_LIB)]
create_rc_corner -name rc_typical -qx_tech_file $::env(W7_QRC)
create_delay_corner -name slow_rc -library_set slow_lib -rc_corner rc_typical
create_constraint_mode -name functional -sdc_files [list $::env(W7_MAPPED_SDC)]
create_analysis_view -name setup_view -constraint_mode functional -delay_corner slow_rc
create_analysis_view -name hold_view -constraint_mode functional -delay_corner slow_rc
set_analysis_view -setup [list setup_view] -hold [list hold_view]
