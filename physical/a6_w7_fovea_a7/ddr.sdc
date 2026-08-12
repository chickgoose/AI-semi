create_clock -name ref_clk -period 16.000 -waveform {0.000 8.000} [get_ports ref_clk_i]
create_clock -name sample_clk -period 16.000 -waveform {4.000 12.000} [get_ports sample_clk_i]
create_generated_clock -name ddr_link_clk \
  -source [get_ports sample_clk_i] -divide_by 1 \
  [get_pins endpoint/tx/clock_boundary/clock_o]
create_clock -name reset_release_clk -period 16.000 -waveform {13.000 15.000}

set_clock_uncertainty 0.500 [get_clocks {ref_clk sample_clk ddr_link_clk reset_release_clk}]
set_input_delay 1.000 -clock ref_clk [get_ports source_valid]
set_input_delay 0.000 -min -clock reset_release_clk [get_ports rst_n]
set_input_delay 0.000 -max -clock reset_release_clk [get_ports rst_n]
set_input_transition 0.100 [get_ports {source_valid rst_n}]
set_output_delay 1.000 -clock ref_clk \
  [get_ports {source_ready retire_addr_o retire_valid_o drain_idle_o protocol_fault_o}]
set_output_delay 1.000 -clock ddr_link_clk [get_ports {burst_clk_o burst_data_o}]
set_load 0.010 [all_outputs]

# rst_n is deliberately not false-pathed: asynchronous reset arcs retain
# recovery/removal timing, while its external release is constrained above.
