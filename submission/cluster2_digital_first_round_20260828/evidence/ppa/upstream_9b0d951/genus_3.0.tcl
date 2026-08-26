set DESIGN   aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity
set RTL_LIST {rtl/arbiter2.v rtl/arbiter4_tree.v rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v}
set SDC_FILE syn/pnr/resynth_steal_buf_polarity/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0.sdc
set LIB_FILE /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
set OUT_DIR  syn/pnr/resynth_steal_buf_polarity

set_db library $LIB_FILE
set_db lp_insert_clock_gating true

read_hdl $RTL_LIST
elaborate $DESIGN
read_sdc $SDC_FILE

syn_generic
syn_map
syn_opt

report_area   > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0_area.rpt
report_timing > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0_gtiming.rpt
report_power  > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0_gpower.rpt

write_hdl > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0_netlist.v
write_sdc > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.0_out.sdc
exit
