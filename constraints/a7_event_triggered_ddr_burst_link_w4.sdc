# Frozen candidate-only timing contract for the W4 A7 DDR link.
# Units: nanoseconds. This is a constraint specification, not PVT sign-off.
set A7_W4_PERIOD_NS          16.000
set A7_W4_HALF_NS             8.000
set A7_W4_PHASE_NS            4.000
set A7_W4_MIN_PULSE_NS        7.000
set A7_W4_MAX_SKEW_NS         0.500
set A7_W4_IO_DELAY_NS         1.000

create_clock -name a7_ref_clk -period $A7_W4_PERIOD_NS \
  -waveform {0.000 8.000} [get_ports ref_clk_i]
create_clock -name a7_sample_clk -period $A7_W4_PERIOD_NS \
  -waveform {4.000 12.000} [get_ports sample_clk_i]

# burst_clk_o is a gated, phase-preserving copy of sample_clk_i. ASIC flow
# must replace a7_w4_icg_boundary with a characterized ICG and propagate it.
create_generated_clock -name a7_burst_clk \
  -source [get_ports sample_clk_i] -divide_by 1 -combinational \
  [get_ports burst_clk_o]

set_clock_uncertainty $A7_W4_MAX_SKEW_NS [get_clocks a7_sample_clk]
set_clock_uncertainty $A7_W4_MAX_SKEW_NS [get_clocks a7_burst_clk]
set_min_pulse_width -high $A7_W4_MIN_PULSE_NS [get_clocks a7_sample_clk]
set_min_pulse_width -low  $A7_W4_MIN_PULSE_NS [get_clocks a7_sample_clk]
set_min_pulse_width -high $A7_W4_MIN_PULSE_NS [get_clocks a7_burst_clk]
set_min_pulse_width -low  $A7_W4_MIN_PULSE_NS [get_clocks a7_burst_clk]

set_input_delay $A7_W4_IO_DELAY_NS -clock a7_ref_clk \
  [get_ports {event_valid_i event_addr_i[*]}]
set_output_delay $A7_W4_IO_DELAY_NS -clock a7_burst_clk \
  [get_ports {burst_data_o[*] retire_addr_o[*] retire_toggle_o}]

# rst_n is asynchronous. Do not false-path it here: a blanket reset false path
# can suppress the recovery/removal evidence that W4 still requires. The target
# flow must constrain reset release, retain library recovery/removal checks, and
# run RDC analysis. Its functional contract permits assertion only after drain
# with burst_clk_o low. No core synchronizer is included for retire_* outputs.
