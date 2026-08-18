# REDRED single-edge endpoint screening constraints.
#
# These numbers are TEAM_PLACEHOLDER_SCREENING_ONLY. They are deliberately
# supplied through an explicit environment class so that copying this SDC into
# a run cannot silently turn them into organizer, pad, package, or signoff
# claims. The qualification gate always holds this contract from candidate GO.

if {![info exists ::env(SE_CONSTRAINT_CLASS)] ||
    $::env(SE_CONSTRAINT_CLASS) ne "TEAM_PLACEHOLDER_SCREENING_ONLY"} {
  error "SE_CONSTRAINT_CLASS must explicitly acknowledge screening placeholders"
}
foreach name {
  SE_PERIOD_NS SE_CLOCK_UNCERTAINTY_NS
  SE_INPUT_DELAY_MIN_NS SE_INPUT_DELAY_MAX_NS
  SE_OUTPUT_DELAY_MIN_NS SE_OUTPUT_DELAY_MAX_NS
  SE_INPUT_TRANSITION_NS SE_OUTPUT_LOAD_PF
  SE_MIN_PULSE_HIGH_NS SE_MIN_PULSE_LOW_NS
} {
  if {![info exists ::env($name)] ||
      ![string is double -strict $::env($name)] || $::env($name) < 0.0} {
    error "missing or invalid nonnegative placeholder $name"
  }
}
if {$::env(SE_PERIOD_NS) <= 0.0} {
  error "SE_PERIOD_NS must be positive"
}

set se_clock_port [get_ports clk_i]
if {[sizeof_collection $se_clock_port] != 1} {
  error "complete endpoint must expose exactly one clk_i port"
}
create_clock -name se_primary_clk -period $::env(SE_PERIOD_NS) \
  -waveform [list 0.0 [expr {$::env(SE_PERIOD_NS) / 2.0}]] $se_clock_port
set_clock_uncertainty $::env(SE_CLOCK_UNCERTAINTY_NS) \
  [get_clocks se_primary_clk]
set_min_pulse_width -high $::env(SE_MIN_PULSE_HIGH_NS) \
  [get_clocks se_primary_clk]
set_min_pulse_width -low $::env(SE_MIN_PULSE_LOW_NS) \
  [get_clocks se_primary_clk]

set se_nonclock_inputs [remove_from_collection [all_inputs] $se_clock_port]
if {[sizeof_collection $se_nonclock_inputs] == 0} {
  error "complete endpoint has no constrained non-clock inputs"
}
set se_outputs [all_outputs]
if {[sizeof_collection $se_outputs] == 0} {
  error "complete endpoint has no constrained outputs"
}
set_input_delay -clock se_primary_clk -min $::env(SE_INPUT_DELAY_MIN_NS) \
  $se_nonclock_inputs
set_input_delay -clock se_primary_clk -max $::env(SE_INPUT_DELAY_MAX_NS) \
  $se_nonclock_inputs
set_input_transition $::env(SE_INPUT_TRANSITION_NS) $se_nonclock_inputs
set_output_delay -clock se_primary_clk -min $::env(SE_OUTPUT_DELAY_MIN_NS) \
  $se_outputs
set_output_delay -clock se_primary_clk -max $::env(SE_OUTPUT_DELAY_MAX_NS) \
  $se_outputs
set_load $::env(SE_OUTPUT_LOAD_PF) $se_outputs

if {[sizeof_collection [get_clocks *]] != 1} {
  error "single-edge endpoint SDC created more than one clock"
}
