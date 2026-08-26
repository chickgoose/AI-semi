create_library_set -name libset_slow -timing "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib"
create_rc_corner -name rc_typical -qrc_tech /home/aiasic26911/gsclib045_all_v4.7/gsclib045/qrc/qx/gpdk045.tch
create_delay_corner -name delay_slow -library_set libset_slow -rc_corner rc_typical
create_constraint_mode -name constraints_default -sdc_files {syn/pnr/resynth_steal_buf_polarity/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_4.0_out.sdc}
create_analysis_view -name view_slow -constraint_mode constraints_default -delay_corner delay_slow
set_analysis_view -setup {view_slow} -hold {view_slow}
