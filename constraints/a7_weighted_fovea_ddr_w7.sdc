# W7 digital-submission timing contract.  These constraints freeze the
# phase-related R1 interface; they are not post-route or PVT sign-off.
set A7_W7_PERIOD_NS       16.000
set A7_W7_PHASE_NS         4.000
set A7_W7_MIN_PULSE_NS     7.000
set A7_W7_MAX_SKEW_NS      0.500
set A7_W7_IO_DELAY_NS      1.000

create_clock -name a7_ref_clk -period $A7_W7_PERIOD_NS \
  -waveform {0.000 8.000} [get_ports ref_clk_i]
create_clock -name a7_sample_clk -period $A7_W7_PERIOD_NS \
  -waveform {4.000 12.000} [get_ports sample_clk_i]
create_generated_clock -name a7_burst_clk \
  -source [get_ports sample_clk_i] -divide_by 1 -combinational \
  [get_ports burst_clk_o]

set_clock_uncertainty $A7_W7_MAX_SKEW_NS [get_clocks a7_sample_clk]
set_clock_uncertainty $A7_W7_MAX_SKEW_NS [get_clocks a7_burst_clk]
set_min_pulse_width -high $A7_W7_MIN_PULSE_NS [get_clocks a7_sample_clk]
set_min_pulse_width -low  $A7_W7_MIN_PULSE_NS [get_clocks a7_sample_clk]
set_min_pulse_width -high $A7_W7_MIN_PULSE_NS [get_clocks a7_burst_clk]
set_min_pulse_width -low  $A7_W7_MIN_PULSE_NS [get_clocks a7_burst_clk]

set_input_delay $A7_W7_IO_DELAY_NS -clock a7_ref_clk \
  [get_ports source_valid[*]]
set_output_delay $A7_W7_IO_DELAY_NS -clock a7_ref_clk \
  [get_ports {source_ready[*] retire_addr_o[*] retire_valid_o drain_idle_o protocol_fault_o}]
set_output_delay $A7_W7_IO_DELAY_NS -clock a7_burst_clk \
  [get_ports burst_data_o[*]]
set_output_delay $A7_W7_IO_DELAY_NS -clock a7_burst_clk -clock_fall -add_delay \
  [get_ports burst_data_o[*]]

# rst_n assertion is asynchronous.  A blanket reset false path is forbidden:
# the target flow must retain recovery/removal and RDC checks.  Legal assertion
# is only after drain_idle_o with the forwarded clock low.
