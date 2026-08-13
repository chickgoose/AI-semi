# P6 phase-related multi-clock constraint template.
#
# ref_clk_i and sample_clk_i share one physical source and one period.  The
# sample clock is shifted by one quarter period, matching the committed P6
# digital contract.  p6_clk_o is a gated, forwarded copy of sample_clk_i.
# These clocks are intentionally related; do not place them in asynchronous
# clock groups or false-path the crossings between them.

proc p6_require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "P6 required environment variable is unset: $name"
  }
  return $::env($name)
}

proc p6_require_singleton {label collection} {
  set count [sizeof_collection $collection]
  if {$count != 1} {
    error "P6 expected one $label object, found $count"
  }
  return $collection
}

proc p6_require_nonempty {label collection} {
  set count [sizeof_collection $collection]
  if {$count == 0} {
    error "P6 required collection is empty: $label"
  }
  return $collection
}

proc p6_require_nonnegative {label value} {
  if {![string is double -strict $value] || $value < 0.0} {
    error "P6 $label must be a nonnegative number, got '$value'"
  }
  return $value
}

set p6_period_ns [p6_require_env P6_REF_PERIOD_NS]
if {![string is double -strict $p6_period_ns] || $p6_period_ns <= 0.0} {
  error "P6_REF_PERIOD_NS must be positive, got '$p6_period_ns'"
}

set p6_uncertainty_ns [p6_require_nonnegative clock_uncertainty \
  [p6_require_env P6_CLOCK_UNCERTAINTY_NS]]
set p6_input_delay_min_ns [p6_require_nonnegative input_delay_min \
  [p6_require_env P6_INPUT_DELAY_MIN_NS]]
set p6_input_delay_max_ns [p6_require_nonnegative input_delay_max \
  [p6_require_env P6_INPUT_DELAY_MAX_NS]]
set p6_output_delay_min_ns [p6_require_nonnegative output_delay_min \
  [p6_require_env P6_OUTPUT_DELAY_MIN_NS]]
set p6_output_delay_max_ns [p6_require_nonnegative output_delay_max \
  [p6_require_env P6_OUTPUT_DELAY_MAX_NS]]
set p6_reset_delay_min_ns [p6_require_nonnegative reset_delay_min \
  [p6_require_env P6_RESET_DELAY_MIN_NS]]
set p6_reset_delay_max_ns [p6_require_nonnegative reset_delay_max \
  [p6_require_env P6_RESET_DELAY_MAX_NS]]
set p6_input_transition_ns [p6_require_nonnegative input_transition \
  [p6_require_env P6_INPUT_TRANSITION_NS]]
set p6_output_load_pf [p6_require_nonnegative output_load \
  [p6_require_env P6_OUTPUT_LOAD_PF]]
set p6_gate_setup_ns [p6_require_nonnegative clock_gating_setup \
  [p6_require_env P6_CLOCK_GATING_SETUP_NS]]
set p6_gate_hold_ns [p6_require_nonnegative clock_gating_hold \
  [p6_require_env P6_CLOCK_GATING_HOLD_NS]]
set p6_min_pulse_high_ns [p6_require_nonnegative min_pulse_high \
  [p6_require_env P6_MIN_PULSE_HIGH_NS]]
set p6_min_pulse_low_ns [p6_require_nonnegative min_pulse_low \
  [p6_require_env P6_MIN_PULSE_LOW_NS]]

foreach pair {
  {input_delay P6_INPUT_DELAY_MIN_NS P6_INPUT_DELAY_MAX_NS}
  {output_delay P6_OUTPUT_DELAY_MIN_NS P6_OUTPUT_DELAY_MAX_NS}
  {reset_delay P6_RESET_DELAY_MIN_NS P6_RESET_DELAY_MAX_NS}
} {
  lassign $pair label min_name max_name
  if {$::env($min_name) > $::env($max_name)} {
    error "P6 $label minimum exceeds maximum"
  }
}

set p6_half_cycle_ns [expr {$p6_period_ns / 2.0}]
set p6_quarter_cycle_ns [expr {$p6_period_ns / 4.0}]
set p6_three_quarter_cycle_ns [expr {3.0 * $p6_period_ns / 4.0}]
set p6_reset_release_rise_ns [expr {13.0 * $p6_period_ns / 16.0}]
set p6_reset_release_fall_ns [expr {15.0 * $p6_period_ns / 16.0}]

if {$p6_uncertainty_ns >= $p6_quarter_cycle_ns} {
  error "P6 uncertainty consumes the quarter-cycle ref/sample aperture"
}
if {$p6_gate_setup_ns + $p6_gate_hold_ns >= $p6_half_cycle_ns} {
  error "P6 gating setup plus hold consumes the sample-clock low phase"
}
if {$p6_min_pulse_high_ns <= 0.0 ||
    $p6_min_pulse_high_ns > $p6_half_cycle_ns ||
    $p6_min_pulse_low_ns <= 0.0 ||
    $p6_min_pulse_low_ns > $p6_half_cycle_ns} {
  error "P6 minimum pulse requirement must fit within one half-cycle"
}

set p6_ref_port [p6_require_singleton ref_clock_port [get_ports ref_clk_i]]
set p6_sample_port [p6_require_singleton sample_clock_port \
  [get_ports sample_clk_i]]
set p6_reset_port [p6_require_singleton reset_port [get_ports rst_n]]
set p6_clock_port [p6_require_singleton forwarded_clock_port \
  [get_ports p6_clk_o]]
set p6_data_ports [p6_require_nonempty forwarded_data_ports \
  [get_ports p6_data_o*]]
if {[sizeof_collection $p6_data_ports] != 5} {
  error "P6 expected five forwarded data ports"
}

create_clock -name p6_ref_clk -period $p6_period_ns \
  -waveform [list 0.0 $p6_half_cycle_ns] $p6_ref_port
create_clock -name p6_sample_clk -period $p6_period_ns \
  -waveform [list $p6_quarter_cycle_ns $p6_three_quarter_cycle_ns] \
  $p6_sample_port
create_generated_clock -name p6_link_clk -source $p6_sample_port \
  -divide_by 1 $p6_clock_port
create_clock -name p6_reset_release_clk -period $p6_period_ns \
  -waveform [list $p6_reset_release_rise_ns $p6_reset_release_fall_ns]

set p6_ref_clock [p6_require_singleton ref_clock [get_clocks p6_ref_clk]]
set p6_sample_clock [p6_require_singleton sample_clock \
  [get_clocks p6_sample_clk]]
set p6_link_clock [p6_require_singleton generated_link_clock \
  [get_clocks p6_link_clk]]
set p6_reset_release_clock [p6_require_singleton reset_release_clock \
  [get_clocks p6_reset_release_clk]]

set_clock_uncertainty $p6_uncertainty_ns \
  [get_clocks {p6_ref_clk p6_sample_clk p6_link_clk p6_reset_release_clk}]

# The explicit rise/fall waveforms preserve the two half-cycles of every P6
# cell.  Apply pulse-width checks to both source clocks and the gated clock.
foreach clock [list $p6_ref_clock $p6_sample_clock $p6_link_clock] {
  set_min_pulse_width -high $p6_min_pulse_high_ns $clock
  set_min_pulse_width -low $p6_min_pulse_low_ns $clock
}

# Constrain the physical gate enable, not the data plane.  The suffix is common
# to A2/A3/A4 P6 integration tops at the committed source hierarchy.
set p6_gate_enable_pins [p6_require_singleton gate_enable_pin \
  [get_pins -hierarchical *endpoint/tx/frame_active_o]]
set_clock_gating_check -setup $p6_gate_setup_ns -hold $p6_gate_hold_ns \
  $p6_gate_enable_pins

set p6_clock_inputs [get_ports {ref_clk_i sample_clk_i}]
set p6_nonclock_inputs [remove_from_collection [all_inputs] $p6_clock_inputs]
set p6_data_inputs [remove_from_collection $p6_nonclock_inputs $p6_reset_port]
p6_require_nonempty synchronous_input_ports $p6_data_inputs
set_input_delay -min $p6_input_delay_min_ns -clock p6_ref_clk $p6_data_inputs
set_input_delay -max $p6_input_delay_max_ns -clock p6_ref_clk $p6_data_inputs
set_input_transition $p6_input_transition_ns \
  [add_to_collection $p6_data_inputs $p6_reset_port]

# Reset release occurs in the common-low interval (13/16 of a period).  Reset
# is deliberately not false-pathed: library recovery/removal arcs must remain
# visible in ref, sample, and generated-clock analysis views.
set_input_delay -min $p6_reset_delay_min_ns -clock p6_reset_release_clk \
  $p6_reset_port
set_input_delay -max $p6_reset_delay_max_ns -clock p6_reset_release_clk \
  $p6_reset_port
set p6_async_reset_pins [p6_require_nonempty asynchronous_reset_endpoints \
  [all_registers -async_pins]]
set p6_ref_reset_pins [p6_require_nonempty ref_clock_reset_endpoints \
  [all_registers -clock p6_ref_clk -async_pins]]
set p6_link_reset_pins [p6_require_nonempty link_clock_reset_endpoints \
  [all_registers -clock p6_link_clk -async_pins]]

set p6_nonlink_outputs [remove_from_collection [all_outputs] \
  [add_to_collection $p6_clock_port $p6_data_ports]]
p6_require_nonempty ref_domain_output_ports $p6_nonlink_outputs
set_output_delay -min $p6_output_delay_min_ns -clock p6_ref_clk \
  $p6_nonlink_outputs
set_output_delay -max $p6_output_delay_max_ns -clock p6_ref_clk \
  $p6_nonlink_outputs

# P6 data is observed on both edges of the forwarded clock.  Both min/max and
# rise/fall constraints are required; omitting -clock_fall leaves half the DDR
# interface unconstrained.
set_output_delay -min $p6_output_delay_min_ns -clock p6_link_clk $p6_data_ports
set_output_delay -max $p6_output_delay_max_ns -clock p6_link_clk $p6_data_ports
set_output_delay -min $p6_output_delay_min_ns -clock p6_link_clk \
  -clock_fall -add_delay $p6_data_ports
set_output_delay -max $p6_output_delay_max_ns -clock p6_link_clk \
  -clock_fall -add_delay $p6_data_ports
set_load $p6_output_load_pf [all_outputs]

# These assertions catch a generated clock that did not propagate into the RX,
# a lost ref clock, or a reset optimization that removed all asynchronous arcs.
p6_require_nonempty ref_clock_registers [all_registers -clock p6_ref_clk]
p6_require_nonempty link_clock_registers [all_registers -clock p6_link_clk]
p6_require_nonempty asynchronous_reset_endpoints $p6_async_reset_pins
p6_require_nonempty ref_clock_reset_endpoints $p6_ref_reset_pins
p6_require_nonempty link_clock_reset_endpoints $p6_link_reset_pins

puts "P6_MULTICLOCK_SDC_READY period_ns=$p6_period_ns half_ns=$p6_half_cycle_ns quarter_ns=$p6_quarter_cycle_ns"
