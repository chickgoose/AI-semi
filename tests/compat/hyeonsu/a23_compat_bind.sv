`ifdef HYEONSU_BIND_AER
bind aer_tb hyeonsu_aer_monitor u_hyeonsu_aer_monitor();
`endif
`ifdef HYEONSU_BIND_ARBITER
bind dual_level_arbiter_tb hyeonsu_arbiter_monitor u_hyeonsu_arbiter_monitor();
`endif
