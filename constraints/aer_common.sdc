# Common constraint for baseline/improved comparisons. Values come from the
# environment so server paths and official corner data stay outside the repo.
proc require_env {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is not set"
  }
  return $::env($name)
}

set clock_port       [require_env AER_CLOCK_PORT]
set reset_port       [require_env AER_RESET_PORT]
set clock_period_ns  [require_env AER_CLOCK_PERIOD_NS]
set input_delay_ns   [require_env AER_INPUT_DELAY_NS]
set output_delay_ns  [require_env AER_OUTPUT_DELAY_NS]
set uncertainty_ns   [require_env AER_CLOCK_UNCERTAINTY_NS]
set output_load_pf   [require_env AER_LOAD_PF]

create_clock -name aer_clk -period $clock_period_ns [get_ports $clock_port]
set_clock_uncertainty $uncertainty_ns [get_clocks aer_clk]

set data_inputs [remove_from_collection [all_inputs] [get_ports $clock_port]]
set non_clock_inputs [remove_from_collection $data_inputs [get_ports $reset_port]]
set_input_delay  $input_delay_ns  -clock aer_clk $non_clock_inputs
set_output_delay $output_delay_ns -clock aer_clk [all_outputs]
set_load $output_load_pf [all_outputs]

# Reset is asynchronous in the proposed contract; do not time it as data.
if {[sizeof_collection [get_ports $reset_port]] > 0} {
  set_false_path -from [get_ports $reset_port]
}

# Set AER_DRIVER_CELL only after the official library/corner is confirmed.
if {[info exists ::env(AER_DRIVER_CELL)] && $::env(AER_DRIVER_CELL) ne ""} {
  set_driving_cell -lib_cell $::env(AER_DRIVER_CELL) $non_clock_inputs
}
