# A9 W5 ASIC constraint template.  This is a fail-closed mapping plan, not STA
# evidence or sign-off.  Capacitance values use the active target library unit.
set A9_W5_PERIOD_NS 16.000
set A9_W5_HALF_NS 8.000
set A9_W5_PHASE_NS 4.000
set A9_W5_MIN_PULSE_NS 7.000

proc a9_w5_require_positive_variable {variable_name} {
  upvar #0 $variable_name value
  if {![info exists value]} {
    error "A9 W5 requires target-specific $variable_name; missing/default values are prohibited"
  }
  if {[catch {expr {double($value)}} numeric_value]} {
    error "A9 W5 requires numeric $variable_name, got '$value'"
  }
  if {$numeric_value <= 0.0} {
    error "A9 W5 requires $variable_name > 0, got '$value'"
  }
}

proc a9_w5_require_nonempty {object_name objects} {
  if {[llength [info commands sizeof_collection]] > 0} {
    set object_count [sizeof_collection $objects]
  } else {
    set object_count [llength $objects]
  }
  if {$object_count <= 0} {
    error "A9 W5 required nonempty collection '$object_name'"
  }
  return $objects
}

foreach required_variable {
  A9_W5_PERIOD_NS
  A9_W5_HALF_NS
  A9_W5_PHASE_NS
  A9_W5_MIN_PULSE_NS
  A9_W5_CLOCK_UNCERTAINTY_NS
  A9_W5_RX_SETUP_BUDGET_NS
  A9_W5_RX_HOLD_BUDGET_NS
  A9_W5_DATA_PAD_LOAD
  A9_W5_CLOCK_PAD_LOAD
  A9_W5_REF_OUTPUT_DELAY_NS
} {
  a9_w5_require_positive_variable $required_variable
}

set a9_w5_ref_port [a9_w5_require_nonempty ref_clk_i [get_ports ref_clk_i]]
set a9_w5_sample_port [a9_w5_require_nonempty sample_clk_i [get_ports sample_clk_i]]
set a9_w5_burst_clock_port [a9_w5_require_nonempty burst_clk_o [get_ports burst_clk_o]]
set a9_w5_burst_data_ports [a9_w5_require_nonempty burst_data_o [get_ports {burst_data_o[*]}]]
set a9_w5_retire_ports [a9_w5_require_nonempty retire_outputs \
  [get_ports {retire_addr_o[*] retire_valid_o drain_idle_o}]]

create_clock -name a9_w5_ref_clk -period $A9_W5_PERIOD_NS \
  -waveform {0.000 8.000} $a9_w5_ref_port
create_clock -name a9_w5_sample_clk -period $A9_W5_PERIOD_NS \
  -waveform {4.000 12.000} $a9_w5_sample_port

set a9_w5_ref_clock [a9_w5_require_nonempty a9_w5_ref_clk \
  [get_clocks a9_w5_ref_clk]]
set a9_w5_sample_clock [a9_w5_require_nonempty a9_w5_sample_clk \
  [get_clocks a9_w5_sample_clk]]

# The target integration must change the sink from the top port to the mapped
# ICG/BUFGCE output pin if the clock does not propagate to this port verbatim.
create_generated_clock -name a9_w5_burst_clk \
  -source $a9_w5_sample_port -divide_by 1 -combinational \
  $a9_w5_burst_clock_port

set a9_w5_burst_clock [a9_w5_require_nonempty a9_w5_burst_clk \
  [get_clocks a9_w5_burst_clk]]

set_clock_uncertainty $A9_W5_CLOCK_UNCERTAINTY_NS \
  [a9_w5_require_nonempty related_sample_burst_clocks \
    [get_clocks {a9_w5_sample_clk a9_w5_burst_clk}]]
set_min_pulse_width -high $A9_W5_MIN_PULSE_NS $a9_w5_burst_clock
set_min_pulse_width -low  $A9_W5_MIN_PULSE_NS $a9_w5_burst_clock

# Both-edge receiver budgets must be declared separately.  The related clock
# waveforms expose the nominal 4 ns ref-rise->burst-rise and
# ref-fall->burst-fall launch/capture windows; do not replace them with a false
# path or a full-cycle multicycle exception.
set_output_delay -clock a9_w5_burst_clk \
  -max $A9_W5_RX_SETUP_BUDGET_NS $a9_w5_burst_data_ports
set_output_delay -clock a9_w5_burst_clk \
  -min [expr {-$A9_W5_RX_HOLD_BUDGET_NS}] $a9_w5_burst_data_ports
set_output_delay -clock a9_w5_burst_clk -clock_fall -add_delay \
  -max $A9_W5_RX_SETUP_BUDGET_NS $a9_w5_burst_data_ports
set_output_delay -clock a9_w5_burst_clk -clock_fall -add_delay \
  -min [expr {-$A9_W5_RX_HOLD_BUDGET_NS}] $a9_w5_burst_data_ports

set_load $A9_W5_DATA_PAD_LOAD $a9_w5_burst_data_ports
set_load $A9_W5_CLOCK_PAD_LOAD $a9_w5_burst_clock_port

# The 42377ca production boundary observes the raw falling-edge toggle at the
# next related ref-clock rise.  The waveform relationship exposes that second
# 4 ns path.  External ref-domain consumers still need a declared output delay.
set_output_delay -clock a9_w5_ref_clk -max $A9_W5_REF_OUTPUT_DELAY_NS \
  $a9_w5_retire_ports

# rst_n is asynchronous and legal only after drain with burst_clk_o low.  No
# reset false path is applied: target recovery/removal and RDC checks remain
# mandatory.  Raw RX state is in the burst domain; the public retire_* observer
# is phase-related to ref_clk_i, not a general synchronizer for unrelated clocks.
