# CONTRACT_SHA256 79d44a39f19ce29ac7437807f94965d70b239030cde2605e46384e212cbf8c43
# EXACT_INPUTS ref_clk_i sample_clk_i rst_n source_pending_i[15:0]
# EXACT_OUTPUTS source_accept_o[15:0] link_clk_o link_data_o retire_valid_o[1:0] retire_addr0_o[3:0] retire_addr1_o[3:0] drain_idle_o protocol_error_o
# FORBIDDEN_ALIASES load_i pending_i source_ready_o protocol_fault_o
create_clock -name ref_clk_i -period 2.000 [get_ports ref_clk_i]
create_clock -name sample_clk_i -period 2.000 -waveform {0.500 1.500} [get_ports sample_clk_i]
# The frozen endpoint contract makes sample_clk_i a phase-related clock.  It
# must not be cut asynchronously from ref_clk_i.
