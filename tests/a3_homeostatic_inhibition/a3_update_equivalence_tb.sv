`timescale 1ns/1ps

module a3_update_equivalence_tb;
  localparam int N = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int VARIANTS = 5;
  localparam int ORIGINAL = 0;
  localparam int CLOCK_ENABLE = 1;
  localparam int INACTIVE_SUPPRESS = 2;
  localparam int SATURATION_SUPPRESS = 3;
  localparam int ALL_SUPPRESS = 4;

  logic clk = 1'b0;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [N];
  logic retire_ready;
  logic [N-1:0] source_ready [VARIANTS];
  logic retire_valid [VARIANTS];
  logic [ADDR_WIDTH-1:0] retire_event [VARIANTS];
  logic [3:0] retire_source [VARIANTS];
  integer cycle_count;
  integer source_index;
  integer variant;
  logic [31:0] lfsr;
  longint write_attempts [VARIANTS];

  always #5 clk = ~clk;

`define A3_EQ_DUT(NAME, INDEX, CE, INACTIVE, SATURATED) \
  a3_homeostatic_inhibition #( \
    .NUM_SOURCES(N), .ADDR_WIDTH(ADDR_WIDTH), \
    .ENABLE_EXACT_CLOCK_ENABLE(CE), \
    .SUPPRESS_INACTIVE_UPDATE(INACTIVE), \
    .SUPPRESS_SATURATED_NOOP(SATURATED) \
  ) NAME ( \
    .clk(clk), .rst_n(rst_n), .source_valid(source_valid), \
    .source_ready(source_ready[INDEX]), .source_event(source_event), \
    .retire_valid(retire_valid[INDEX]), .retire_ready(retire_ready), \
    .retire_event(retire_event[INDEX]), .retire_source(retire_source[INDEX]) \
  )

  `A3_EQ_DUT(original, ORIGINAL, 1'b0, 1'b0, 1'b0);
  `A3_EQ_DUT(clock_enabled, CLOCK_ENABLE, 1'b1, 1'b0, 1'b0);
  `A3_EQ_DUT(inactive_suppressed, INACTIVE_SUPPRESS, 1'b0, 1'b1, 1'b0);
  `A3_EQ_DUT(saturation_suppressed, SATURATION_SUPPRESS, 1'b0, 1'b0, 1'b1);
  `A3_EQ_DUT(all_suppressed, ALL_SUPPRESS, 1'b1, 1'b1, 1'b1);

`undef A3_EQ_DUT

  task automatic compare_variant(input integer checked_variant);
    begin
      if (source_ready[checked_variant] !== source_ready[ORIGINAL] ||
          retire_valid[checked_variant] !== retire_valid[ORIGINAL] ||
          retire_event[checked_variant] !== retire_event[ORIGINAL] ||
          retire_source[checked_variant] !== retire_source[ORIGINAL])
        $fatal(1, "A3 equivalence I/O mismatch cycle=%0d variant=%0d",
               cycle_count, checked_variant);
    end
  endtask

  always @(negedge clk) begin
    if (rst_n) begin
      compare_variant(CLOCK_ENABLE);
      compare_variant(INACTIVE_SUPPRESS);
      compare_variant(SATURATION_SUPPRESS);
      compare_variant(ALL_SUPPRESS);
      for (source_index = 0; source_index < N;
           source_index = source_index + 1) begin
        if (clock_enabled.membrane[source_index] !==
              original.membrane[source_index] ||
            inactive_suppressed.membrane[source_index] !==
              original.membrane[source_index] ||
            saturation_suppressed.membrane[source_index] !==
              original.membrane[source_index] ||
            all_suppressed.membrane[source_index] !==
              original.membrane[source_index])
          $fatal(1, "A3 membrane mismatch cycle=%0d source=%0d",
                 cycle_count, source_index);
      end
      if (clock_enabled.homeostasis !== original.homeostasis ||
          inactive_suppressed.homeostasis !== original.homeostasis ||
          saturation_suppressed.homeostasis !== original.homeostasis ||
          all_suppressed.homeostasis !== original.homeostasis ||
          clock_enabled.phase !== original.phase ||
          inactive_suppressed.phase !== original.phase ||
          saturation_suppressed.phase !== original.phase ||
          all_suppressed.phase !== original.phase)
        $fatal(1, "A3 global-state mismatch cycle=%0d", cycle_count);

      for (source_index = 0; source_index < N;
           source_index = source_index + 1) begin
        write_attempts[ORIGINAL] = write_attempts[ORIGINAL] +
          original.membrane_write_enable[source_index];
        write_attempts[CLOCK_ENABLE] = write_attempts[CLOCK_ENABLE] +
          clock_enabled.membrane_write_enable[source_index];
        write_attempts[INACTIVE_SUPPRESS] =
          write_attempts[INACTIVE_SUPPRESS] +
          inactive_suppressed.membrane_write_enable[source_index];
        write_attempts[SATURATION_SUPPRESS] =
          write_attempts[SATURATION_SUPPRESS] +
          saturation_suppressed.membrane_write_enable[source_index];
        write_attempts[ALL_SUPPRESS] = write_attempts[ALL_SUPPRESS] +
          all_suppressed.membrane_write_enable[source_index];
      end

      cycle_count = cycle_count + 1;
      if (cycle_count < 32) begin
        source_valid = '0;
        retire_ready = 1'b1;
      end else if (cycle_count < 128) begin
        source_valid = '1;
        retire_ready = 1'b1;
      end else if (cycle_count < 224) begin
        source_valid = '1;
        retire_ready = 1'b0;
      end else if (cycle_count < 320) begin
        source_valid = '1;
        retire_ready = 1'b1;
      end else begin
        lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        source_valid = lfsr[15:0];
        retire_ready = lfsr[19] | lfsr[23];
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = 1'b1;
    cycle_count = 0;
    lfsr = 32'h1a3c_5e7d;
    for (source_index = 0; source_index < N;
         source_index = source_index + 1)
      source_event[source_index] = ADDR_WIDTH'(source_index);
    for (variant = 0; variant < VARIANTS; variant = variant + 1)
      write_attempts[variant] = 0;
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    repeat (1400) @(posedge clk);
    @(negedge clk);

    if (!(write_attempts[CLOCK_ENABLE] < write_attempts[ORIGINAL]))
      $fatal(1, "A3 exact clock-enable suppressed no writes");
    if (!(write_attempts[INACTIVE_SUPPRESS] < write_attempts[ORIGINAL]))
      $fatal(1, "A3 inactive suppression suppressed no writes");
    if (!(write_attempts[SATURATION_SUPPRESS] < write_attempts[ORIGINAL]))
      $fatal(1, "A3 saturation suppression suppressed no writes");
    if (!(write_attempts[ALL_SUPPRESS] <= write_attempts[CLOCK_ENABLE] &&
          write_attempts[ALL_SUPPRESS] <= write_attempts[INACTIVE_SUPPRESS] &&
          write_attempts[ALL_SUPPRESS] <= write_attempts[SATURATION_SUPPRESS]))
      $fatal(1, "A3 combined suppression is not minimal");

    $display("A3_UPDATE_EQUIVALENCE_PASS cycles=%0d original_writes=%0d clock_enable=%0d inactive=%0d saturated=%0d all=%0d",
             cycle_count, write_attempts[ORIGINAL],
             write_attempts[CLOCK_ENABLE], write_attempts[INACTIVE_SUPPRESS],
             write_attempts[SATURATION_SUPPRESS],
             write_attempts[ALL_SUPPRESS]);
    $finish;
  end
endmodule
