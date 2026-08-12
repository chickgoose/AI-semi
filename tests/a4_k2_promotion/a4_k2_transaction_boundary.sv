`timescale 1ns/1ps

// A4-owned normalization used only by the transaction replay.  Candidate
// bindings provide an atomic count/address owner below this module.  The
// occurrence identity payload never reaches that owner.
module a4_k2_transaction_boundary (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_valid,
  input  logic [31:0] source_event [16],
  output logic [15:0] source_ready,

  output logic [1:0]  accept_valid,
  output logic [3:0]  accept_source [2],
  output logic [31:0] accept_event [2],

  output logic [1:0]  retire_valid,
  input  logic [1:0]  retire_ready,
  output logic [3:0]  retire_source [2],
  output logic [31:0] retire_event [2],
  output logic        drain_idle
);
  logic [1:0] owner_count;
  logic [3:0] owner_addr0;
  logic [3:0] owner_addr1;
  logic       atomic_ready;
  logic       owner_idle;
  integer lane;

  always @* begin
    case (owner_count)
      2'd0: atomic_ready = 1'b1;
      2'd1: atomic_ready = retire_ready[0];
      2'd2: atomic_ready = &retire_ready;
      default: atomic_ready = 1'b0;
    endcase
  end

  a4_k2_owner owner (
    .clk          (clk),
    .rst          (rst),
    .source_pending(source_valid),
    .grant_count  (owner_count),
    .grant_addr0  (owner_addr0),
    .grant_addr1  (owner_addr1),
    .bundle_ready (atomic_ready),
    .drain_idle   (owner_idle)
  );

  always @* begin
    source_ready = '0;
    accept_valid = '0;
    retire_valid = '0;
    for (lane = 0; lane < 2; lane = lane + 1) begin
      accept_source[lane] = '0;
      accept_event[lane] = '0;
      retire_source[lane] = '0;
      retire_event[lane] = '0;
    end

    if (!rst) begin
      if (owner_count >= 1) begin
        retire_valid[0] = 1'b1;
        retire_source[0] = owner_addr0;
        retire_event[0] = source_event[owner_addr0];
        if (atomic_ready) begin
          source_ready[owner_addr0] = 1'b1;
          accept_valid[0] = 1'b1;
          accept_source[0] = owner_addr0;
          accept_event[0] = source_event[owner_addr0];
        end
      end
      if (owner_count == 2) begin
        retire_valid[1] = 1'b1;
        retire_source[1] = owner_addr1;
        retire_event[1] = source_event[owner_addr1];
        if (atomic_ready) begin
          source_ready[owner_addr1] = 1'b1;
          accept_valid[1] = 1'b1;
          accept_source[1] = owner_addr1;
          accept_event[1] = source_event[owner_addr1];
        end
      end
    end
    drain_idle = !rst && owner_idle && (source_valid == 16'b0) &&
                 (owner_count == 0);
  end

`ifndef SYNTHESIS
  always @(posedge clk) begin
    if (!rst) begin
      if (owner_count > 2)
        $fatal(1, "A4_K2_BOUNDARY illegal owner count=%0d", owner_count);
      if (retire_valid == 2'b10 || accept_valid == 2'b10)
        $fatal(1, "A4_K2_BOUNDARY lane hole");
      if ((owner_count == 2) && (owner_addr0 == owner_addr1))
        $fatal(1, "A4_K2_BOUNDARY duplicate ordered owner address=%0d", owner_addr0);
      if ((owner_count == 2) && (retire_ready[0] !== retire_ready[1]))
        $fatal(1, "A4_K2_BOUNDARY asymmetric ready is outside atomic contract");
      if ((source_ready & ~source_valid) != 0)
        $fatal(1, "A4_K2_BOUNDARY accepted a non-pending source");
    end
  end
`endif
endmodule
