# arbiter4 smoke-test constraints. Clock period matches the teammate's
# PPA run (see teammate_handoff_summary.txt) so numbers are comparable later.
create_clock -name clk -period 3.5 [get_ports clk]
set_clock_uncertainty 0.100 [get_clocks clk]
set_input_delay  -clock clk 0.250 [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay -clock clk 0.250 [all_outputs]
set_load 0.010 [all_outputs]
