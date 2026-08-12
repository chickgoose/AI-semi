# Actual Innovus 23.14 failure form: -check_type cannot be combined with
# -late/-early, even with both setup_view and hold_view active.
set_analysis_view -setup [list setup_view] -hold [list hold_view]
report_timing -late -check_type recovery -max_paths 50 > timing_recovery.rpt
report_timing -early -check_type removal -max_paths 50 > timing_removal.rpt
