# A9 W5 ASIC constraint template.  This is a fail-closed mapping plan, not STA
# evidence or sign-off.  Capacitance values use the active target library unit.
set A9_W5_PERIOD_NS 16.000
set A9_W5_HALF_NS 8.000
set A9_W5_PHASE_NS 4.000
set A9_W5_MIN_PULSE_NS 7.000

foreach required_variable {
  A9_W5_CLOCK_UNCERTAINTY_NS
  A9_W5_RX_SETUP_BUDGET_NS
  A9_W5_RX_HOLD_BUDGET_NS
  A9_W5_DATA_PAD_LOAD
  A9_W5_CLOCK_PAD_LOAD
  A9_W5_REF_OUTPUT_DELAY_NS
} {
  if {![info exists $required_variable]} {
    error "A9 W5 requires target-specific $required_variable; zero/default load is prohibited"
  }
}

create_clock -name a9_w5_ref_clk -period $A9_W5_PERIOD_NS \
  -waveform {0.000 8.000} [get_ports ref_clk_i]
create_clock -name a9_w5_sample_clk -period $A9_W5_PERIOD_NS \
  -waveform {4.000 12.000} [get_ports sample_clk_i]

# The target integration must change the sink from the top port to the mapped
# ICG/BUFGCE output pin if the clock does not propagate to this port verbatim.
create_generated_clock -name a9_w5_burst_clk \
  -source [get_ports sample_clk_i] -divide_by 1 -combinational \
  [get_ports burst_clk_o]

set_clock_uncertainty $A9_W5_CLOCK_UNCERTAINTY_NS \
  [get_clocks {a9_w5_sample_clk a9_w5_burst_clk}]
set_min_pulse_width -high $A9_W5_MIN_PULSE_NS [get_clocks a9_w5_burst_clk]
set_min_pulse_width -low  $A9_W5_MIN_PULSE_NS [get_clocks a9_w5_burst_clk]

# Both-edge receiver budgets must be declared separately.  The related clock
# waveforms expose the nominal 4 ns ref-rise->burst-rise and
# ref-fall->burst-fall launch/capture windows; do not replace them with a false
# path or a full-cycle multicycle exception.
set_output_delay -clock a9_w5_burst_clk \
  -max $A9_W5_RX_SETUP_BUDGET_NS [get_ports {burst_data_o[*]}]
set_output_delay -clock a9_w5_burst_clk \
  -min [expr {-$A9_W5_RX_HOLD_BUDGET_NS}] [get_ports {burst_data_o[*]}]
set_output_delay -clock a9_w5_burst_clk -clock_fall -add_delay \
  -max $A9_W5_RX_SETUP_BUDGET_NS [get_ports {burst_data_o[*]}]
set_output_delay -clock a9_w5_burst_clk -clock_fall -add_delay \
  -min [expr {-$A9_W5_RX_HOLD_BUDGET_NS}] [get_ports {burst_data_o[*]}]

set_load $A9_W5_DATA_PAD_LOAD [get_ports {burst_data_o[*]}]
set_load $A9_W5_CLOCK_PAD_LOAD [get_ports burst_clk_o]

# The 42377ca production boundary observes the raw falling-edge toggle at the
# next related ref-clock rise.  The waveform relationship exposes that second
# 4 ns path.  External ref-domain consumers still need a declared output delay.
set_output_delay -clock a9_w5_ref_clk -max $A9_W5_REF_OUTPUT_DELAY_NS \
  [get_ports {retire_addr_o[*] retire_valid_o drain_idle_o}]

# rst_n is asynchronous and legal only after drain with burst_clk_o low.  No
# reset false path is applied: target recovery/removal and RDC checks remain
# mandatory.  Raw RX state is in the burst domain; the public retire_* observer
# is phase-related to ref_clk_i, not a general synchronizer for unrelated clocks.
