`timescale 1ns/1ps

module a3_homeostatic_inhibition #(
  parameter int NUM_SOURCES       = 16,
  parameter int ADDR_WIDTH        = 16,
  parameter int SOURCE_WIDTH      = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int URGENCY_WIDTH     = 6,
  parameter int HOME_WIDTH        = 4,
  parameter int LEAK              = 1,
  parameter int GAIN_LOW_ACTIVITY = 6,
  parameter int GAIN_HIGH_ACTIVITY= 5,
  parameter int INHIBIT_LOW       = 1,
  parameter int INHIBIT_HIGH      = 2,
  parameter int HOME_LOW_ACTIVE   = 2,
  parameter int HOME_HIGH_ACTIVE  = 4,
  parameter int THRESHOLD_BASE    = 8,
  parameter int THRESHOLD_SHIFT   = 1
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic [NUM_SOURCES-1:0]       source_valid,
  output logic [NUM_SOURCES-1:0]       source_ready,
  input  logic [ADDR_WIDTH-1:0]        source_event [NUM_SOURCES],
  output logic                         retire_valid,
  input  logic                         retire_ready,
  output logic [ADDR_WIDTH-1:0]        retire_event,
  output logic [SOURCE_WIDTH-1:0]      retire_source
);
  localparam int URGENCY_MAX = (1 << URGENCY_WIDTH) - 1;
  localparam int HOME_MAX = (1 << HOME_WIDTH) - 1;
  localparam int THRESHOLD_MAX = THRESHOLD_BASE +
                                 (HOME_MAX << THRESHOLD_SHIFT);

  // These state names are intentionally visible to the candidate-only
  // activity testbench.  They are not functional/debug output ports.
  logic [URGENCY_WIDTH-1:0] membrane [NUM_SOURCES];
  logic [HOME_WIDTH-1:0] homeostasis;
  logic [SOURCE_WIDTH-1:0] phase;

  integer active_count;
  integer excitation_gain;
  integer inhibition_pulse;
  integer protected_threshold;
  integer selected_source;
  integer scan_offset;
  integer scan_source;
  integer max_membrane;
  integer source_index;
  logic protected_found;
  logic output_slot_available;
  logic grant_valid;

  function automatic logic [URGENCY_WIDTH-1:0] clamp_membrane(
    input integer value
  );
    if (value < 0)
      clamp_membrane = '0;
    else if (value > URGENCY_MAX)
      clamp_membrane = URGENCY_WIDTH'(URGENCY_MAX);
    else
      clamp_membrane = URGENCY_WIDTH'(value);
  endfunction

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A3 requires NUM_SOURCES >= 1");
    if (NUM_SOURCES > (1 << SOURCE_WIDTH))
      $fatal(1, "A3 SOURCE_WIDTH cannot represent NUM_SOURCES");
    if (GAIN_HIGH_ACTIVITY <= (LEAK + INHIBIT_HIGH))
      $fatal(1, "A3 fairness requires positive inhibited membrane progress");
    if (THRESHOLD_MAX > URGENCY_MAX)
      $fatal(1, "A3 maximum threshold must fit the membrane state");
    if (HOME_LOW_ACTIVE > HOME_HIGH_ACTIVE)
      $fatal(1, "A3 homeostatic hysteresis thresholds are reversed");
  end

  always @* begin
    active_count = 0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if (source_valid[source_index])
        active_count = active_count + 1;
    end

    if (homeostasis[HOME_WIDTH-1]) begin
      excitation_gain = GAIN_HIGH_ACTIVITY;
      inhibition_pulse = INHIBIT_HIGH;
    end else begin
      excitation_gain = GAIN_LOW_ACTIVITY;
      inhibition_pulse = INHIBIT_LOW;
    end
    protected_threshold = THRESHOLD_BASE +
                          (integer'(homeostasis) << THRESHOLD_SHIFT);

    protected_found = 1'b0;
    selected_source = -1;
    max_membrane = -1;

    // First choose within the dynamically protected population.  Rotation is
    // only a symmetry-safe scan origin; it is not added to any source score.
    for (scan_offset = 0; scan_offset < NUM_SOURCES;
         scan_offset = scan_offset + 1) begin
      scan_source = integer'(phase) + scan_offset;
      if (scan_source >= NUM_SOURCES)
        scan_source = scan_source - NUM_SOURCES;
      if (!protected_found && source_valid[scan_source] &&
          (integer'(membrane[scan_source]) >= protected_threshold)) begin
        protected_found = 1'b1;
        selected_source = scan_source;
      end
    end

    // Below threshold the LIF membrane, rather than address rank, determines
    // the winner.  Cyclic scan order resolves exact equal-voltage ties.
    if (!protected_found) begin
      for (scan_offset = 0; scan_offset < NUM_SOURCES;
           scan_offset = scan_offset + 1) begin
        scan_source = integer'(phase) + scan_offset;
        if (scan_source >= NUM_SOURCES)
          scan_source = scan_source - NUM_SOURCES;
        if (source_valid[scan_source] &&
            ((selected_source < 0) ||
             (integer'(membrane[scan_source]) > max_membrane))) begin
          max_membrane = integer'(membrane[scan_source]);
          selected_source = scan_source;
        end
      end
    end

    output_slot_available = !retire_valid || retire_ready;
    grant_valid = output_slot_available && (selected_source >= 0);
    source_ready = '0;
    if (grant_valid)
      source_ready[selected_source] = 1'b1;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      retire_valid <= 1'b0;
      retire_event <= '0;
      retire_source <= '0;
      homeostasis <= '0;
      phase <= '0;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1)
        membrane[source_index] <= '0;
    end else begin
      if (active_count > HOME_HIGH_ACTIVE) begin
        if (homeostasis != HOME_WIDTH'(HOME_MAX))
          homeostasis <= homeostasis + 1'b1;
      end else if (active_count < HOME_LOW_ACTIVE) begin
        if (homeostasis != 0)
          homeostasis <= homeostasis - 1'b1;
      end

      if (output_slot_available) begin
        if (selected_source >= 0) begin
          retire_valid <= 1'b1;
          retire_event <= source_event[selected_source];
          retire_source <= SOURCE_WIDTH'(selected_source);
          if (selected_source == NUM_SOURCES-1)
            phase <= '0;
          else
            phase <= SOURCE_WIDTH'(selected_source + 1);
        end else begin
          retire_valid <= 1'b0;
        end
      end

      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        if (grant_valid && (source_index == selected_source)) begin
          membrane[source_index] <= '0;
        end else if (source_valid[source_index]) begin
          membrane[source_index] <= clamp_membrane(
            integer'(membrane[source_index]) + excitation_gain - LEAK -
            (grant_valid ? inhibition_pulse : 0));
        end else begin
          if (integer'(membrane[source_index]) > LEAK)
            membrane[source_index] <= clamp_membrane(
              integer'(membrane[source_index]) - LEAK);
          else
            membrane[source_index] <= '0;
        end
      end
    end
  end
endmodule
