# REDRED A2/A3 single-edge diagnostic screening point.
# UNCONFIRMED_TEAM_PLACEHOLDER: these I/O/load/clock values have no external
# release authority and can never authorize candidate GO under this contract.
# The diagnostic uses one primary positive-edge clock and no timing exceptions.
create_clock -name single_edge_clk -period 6.500 -waveform {0.000 3.250} [get_ports clk_i]
set_clock_uncertainty 0.250 [get_clocks single_edge_clk]

set nonclock_inputs [remove_from_collection [all_inputs] [get_ports clk_i]]
set_input_delay -clock single_edge_clk -min 0.100 $nonclock_inputs
set_input_delay -clock single_edge_clk -max 0.500 $nonclock_inputs
set_input_transition 0.050 $nonclock_inputs

set_output_delay -clock single_edge_clk -min 0.100 [all_outputs]
set_output_delay -clock single_edge_clk -max 0.500 [all_outputs]
set_load 0.010 [all_outputs]

set_min_pulse_width -high 0.500 [get_clocks single_edge_clk]
set_min_pulse_width -low 0.500 [get_clocks single_edge_clk]
