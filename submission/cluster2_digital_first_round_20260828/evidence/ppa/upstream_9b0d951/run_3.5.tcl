set DESIGN aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity
set OUT_DIR syn/pnr/resynth_steal_buf_polarity
set init_lef_file "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/lef/gsclib045_tech.lef /home/aiasic26911/gsclib045_all_v4.7/gsclib045/lef/gsclib045_macro.lef"
set init_verilog syn/pnr/resynth_steal_buf_polarity/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_netlist.v
set init_top_cell $DESIGN
set init_gnd_net VSS
set init_pwr_net VDD
set init_mmmc_file syn/pnr/resynth_steal_buf_polarity/mmmc_3.5.tcl
init_design
setDesignMode -process 45
set_dont_use [get_lib_cells */BUFX2] true
floorPlan -r 1.0 0.5 10 10 10 10
assignIoPins -pin [dbGet top.terms.name]
globalNetConnect VDD -type pgpin -pin VDD -inst * -verbose
globalNetConnect VSS -type pgpin -pin VSS -inst * -verbose
addRing -nets {VDD VSS} -type core_rings -layer {top Metal6 bottom Metal6 left Metal7 right Metal7} -width 2 -spacing 2 -offset 2
sroute -nets {VDD VSS} -connect {blockPin padPin corePin}
place_opt_design
clock_opt_design
routeDesign
extractRC

report_area  > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_pnr_area.rpt
report_power > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_pnr_power.rpt
report_timing -late  > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_setup_timing.rpt
report_timing -early > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_hold_timing.rpt
catch {check_timing -verbose > $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_check_timing.rpt}
catch {verify_drc -report $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_drc.rpt}
catch {verify_process_antenna -report $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_antenna.rpt}
catch {write_db $OUT_DIR/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5_db}
exit
