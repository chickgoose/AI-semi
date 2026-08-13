# Strict phase-related R1 DDR constraint template. No async/false/multicycle paths.
proc w2_req_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} { error "missing $name" }
  return $::env($name)
}
proc w2_one {label objects} {
  set n [sizeof_collection $objects]
  if {$n != 1} { error "R1 expected exactly one $label, found $n" }
  return $objects
}
proc w2_some {label objects} {
  if {[sizeof_collection $objects] == 0} { error "R1 empty $label" }
  return $objects
}
proc w2_num {name positive} {
  set value [w2_req_env $name]
  if {![string is double -strict $value] || ($positive && $value <= 0.0) || (!$positive && $value < 0.0)} {
    error "invalid $name=$value"
  }
  return $value
}
set period [w2_num W2_REF_PERIOD_NS 1]
set uncertainty [w2_num W2_CLOCK_UNCERTAINTY_NS 0]
set in_min [w2_num W2_INPUT_DELAY_MIN_NS 0]
set in_max [w2_num W2_INPUT_DELAY_MAX_NS 0]
set out_min [w2_num W2_OUTPUT_DELAY_MIN_NS 0]
set out_max [w2_num W2_OUTPUT_DELAY_MAX_NS 0]
set reset_min [w2_num W2_RESET_DELAY_MIN_NS 0]
set reset_max [w2_num W2_RESET_DELAY_MAX_NS 0]
set transition [w2_num W2_INPUT_TRANSITION_NS 1]
set load [w2_num W2_OUTPUT_LOAD_PF 1]
set gate_setup [w2_num W2_CLOCK_GATING_SETUP_NS 1]
set gate_hold [w2_num W2_CLOCK_GATING_HOLD_NS 1]
set pulse_high [w2_num W2_MIN_PULSE_HIGH_NS 1]
set pulse_low [w2_num W2_MIN_PULSE_LOW_NS 1]
if {$in_min > $in_max || $out_min > $out_max || $reset_min > $reset_max} { error "min exceeds max" }
set half [expr {$period / 2.0}]
set quarter [expr {$period / 4.0}]
set three_quarter [expr {3.0 * $period / 4.0}]
set reset_release_rise [expr {13.0 * $period / 16.0}]
set reset_release_fall [expr {15.0 * $period / 16.0}]
if {$uncertainty >= $quarter || $gate_setup + $gate_hold >= $half} { error "timing aperture consumed" }
if {$pulse_high > $half || $pulse_low > $half} { error "pulse width exceeds half cycle" }

set ref_port [w2_one ref_clk_i [get_ports ref_clk_i]]
set sample_port [w2_one sample_clk_i [get_ports sample_clk_i]]
set reset_port [w2_one rst_n [get_ports rst_n]]
set link_clock_port [w2_one link_clk_o [get_ports link_clk_o]]
set link_data_ports [w2_some link_data_o [get_ports link_data_o*]]
set link_icg_eck [w2_one link_icg_ECK [get_pins -hierarchical *w2_ep_icg_0/ECK]]
if {[sizeof_collection $link_data_ports] != 2} { error "R1 requires exactly two DDR data ports" }
create_clock -name r1_ref_clk -period $period -waveform [list 0.0 $half] $ref_port
create_clock -name r1_sample_clk -period $period -waveform [list $quarter $three_quarter] $sample_port
create_generated_clock -name r1_link_clk -source $sample_port -divide_by 1 $link_icg_eck
create_clock -name r1_reset_release_clk -period $period -waveform [list $reset_release_rise $reset_release_fall]
set ref_clock [w2_one r1_ref_clk [get_clocks r1_ref_clk]]
set sample_clock [w2_one r1_sample_clk [get_clocks r1_sample_clk]]
set link_clock [w2_one r1_link_clk [get_clocks r1_link_clk]]
set reset_release_clock [w2_one r1_reset_release_clk [get_clocks r1_reset_release_clk]]
set_clock_uncertainty $uncertainty [get_clocks {r1_ref_clk r1_sample_clk r1_link_clk r1_reset_release_clk}]
foreach clock [list $ref_clock $sample_clock $link_clock] {
  set_min_pulse_width -high $pulse_high $clock
  set_min_pulse_width -low $pulse_low $clock
}
set_clock_gating_check -setup $gate_setup -hold $gate_hold $sample_clock

set clock_inputs [get_ports {ref_clk_i sample_clk_i}]
set nonclock_inputs [remove_from_collection [all_inputs] $clock_inputs]
set data_inputs [remove_from_collection $nonclock_inputs $reset_port]
w2_some synchronous_inputs $data_inputs
set_input_delay -min $in_min -clock r1_ref_clk $data_inputs
set_input_delay -max $in_max -clock r1_ref_clk $data_inputs
set_driving_cell -lib_cell [w2_req_env W2_DRIVE_CELL] $data_inputs
set_input_transition $transition [add_to_collection $data_inputs $reset_port]

# Reset is constrained, never false-pathed. Genus 23.14 reports the
# recovery_falling and removal_falling checks separately in the driver; the
# SDC fail-closes on the actual asynchronous register endpoints.
set_input_delay -min $reset_min -clock r1_reset_release_clk $reset_port
set_input_delay -max $reset_max -clock r1_reset_release_clk $reset_port
set async_reset_pins [w2_some async_reset_endpoints [all_registers -async_pins]]

set link_ports [add_to_collection $link_clock_port $link_data_ports]
set nonlink_outputs [remove_from_collection [all_outputs] $link_ports]
w2_some nonlink_outputs $nonlink_outputs
set_output_delay -min $out_min -clock r1_ref_clk $nonlink_outputs
set_output_delay -max $out_max -clock r1_ref_clk $nonlink_outputs
set_output_delay -min $out_min -clock r1_link_clk $link_data_ports
set_output_delay -max $out_max -clock r1_link_clk $link_data_ports
set_output_delay -min $out_min -clock r1_link_clk -clock_fall -add_delay $link_data_ports
set_output_delay -max $out_max -clock r1_link_clk -clock_fall -add_delay $link_data_ports
set_load $load [all_outputs]
set ref_registers [w2_some ref_registers [all_registers -clock $ref_clock]]
set link_registers [w2_some link_registers [all_registers -clock $link_clock]]
w2_some async_reset_endpoints $async_reset_pins
puts "W2_STRICT_R1_SDC_READY"
