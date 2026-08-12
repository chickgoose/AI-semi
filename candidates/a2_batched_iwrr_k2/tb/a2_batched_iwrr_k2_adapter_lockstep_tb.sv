module a2_batched_iwrr_k2_adapter_lockstep_tb;
  logic clk = 1'b0;
  logic rst_n;
  logic [15:0] source_valid;
  logic [15:0] source_ready;
  logic [255:0] source_event;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready;
  logic [31:0] retire_event;
  logic [7:0] retire_source;
  logic drain_idle;

  a2_batched_iwrr_k2_normalized dut (.*);

  string vectors;
  integer fd, rc, cycle;
  logic [255:0] vi_events;
  logic [15:0] vi_source_valid;
  logic [1:0] vi_retire_ready;
  logic vi_rst_n;
  logic [15:0] ex_source_ready;
  logic [1:0] ex_retire_valid;
  logic [31:0] ex_retire_events;
  logic [7:0] ex_retire_sources;
  logic ex_drain_idle;
  logic [1:0] ex_queue_count;
  logic [7:0] ex_queue_sources;
  logic [31:0] ex_queue_events;
  logic [1:0] ex_owner_count;
  logic [7:0] ex_owner_addresses;
  logic ex_owner_ready;
  logic [3:0] ex_owner_cursor;
  logic [7:0] ex_owner_pointers;
  logic ex_owner_hold;
  logic ex_owner_hold_two;
  logic [7:0] ex_owner_hold_addresses;

  task automatic check_equal(
    input logic [255:0] observed,
    input logic [255:0] expected,
    input string name
  );
    if (observed !== expected) begin
      $display("A2_K2_ADAPTER_LOCKSTEP_FAIL cycle=%0d field=%s observed=%0h expected=%0h",
               cycle, name, observed, expected);
      $fatal(1);
    end
  endtask

  initial begin
    if (!$value$plusargs("VECTORS=%s", vectors))
      $fatal(1, "missing +VECTORS");
    fd = $fopen(vectors, "r");
    if (fd == 0)
      $fatal(1, "cannot open vectors");
    cycle = 0;
    while (!$feof(fd)) begin
      rc = $fscanf(
        fd,
        "%x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x\n",
        vi_rst_n, vi_retire_ready, vi_source_valid, vi_events,
        ex_source_ready, ex_retire_valid, ex_retire_events,
        ex_retire_sources, ex_drain_idle, ex_queue_count,
        ex_queue_sources, ex_queue_events, ex_owner_count,
        ex_owner_addresses, ex_owner_ready, ex_owner_cursor,
        ex_owner_pointers, ex_owner_hold, ex_owner_hold_two,
        ex_owner_hold_addresses
      );
      if (rc == 20) begin
        rst_n = vi_rst_n;
        retire_ready = vi_retire_ready;
        source_valid = vi_source_valid;
        source_event = vi_events;
        #1;
        check_equal(source_ready, ex_source_ready, "source_ready");
        check_equal(retire_valid, ex_retire_valid, "retire_valid");
        check_equal(retire_event, ex_retire_events, "retire_events");
        check_equal(retire_source, ex_retire_sources, "retire_sources");
        check_equal(drain_idle, ex_drain_idle, "drain_idle");
        check_equal(dut.ordered_link.count_q, ex_queue_count, "queue_count");
        check_equal({dut.ordered_link.source1_q, dut.ordered_link.source0_q},
                    ex_queue_sources, "queue_sources");
        check_equal({dut.ordered_link.event1_q, dut.ordered_link.event0_q},
                    ex_queue_events, "queue_events");
        check_equal(dut.native_count, ex_owner_count, "owner_count");
        check_equal({dut.native_addr1, dut.native_addr0},
                    ex_owner_addresses, "owner_addresses");
        check_equal(dut.native_bundle_ready, ex_owner_ready, "owner_ready");
        check_equal(dut.owner.token_cursor_q, ex_owner_cursor, "owner_cursor");
        check_equal({dut.owner.row_ptr_q[3], dut.owner.row_ptr_q[2],
                     dut.owner.row_ptr_q[1], dut.owner.row_ptr_q[0]},
                    ex_owner_pointers, "owner_pointers");
        check_equal(dut.owner.hold_q, ex_owner_hold, "owner_hold");
        if (ex_owner_hold) begin
          check_equal(dut.owner.hold_two_q, ex_owner_hold_two, "owner_hold_two");
          check_equal({dut.owner.hold_addr1_q, dut.owner.hold_addr0_q},
                      ex_owner_hold_addresses, "owner_hold_addresses");
        end
        clk = 1'b1;
        #1;
        clk = 1'b0;
        #1;
        cycle = cycle + 1;
      end else if (rc != -1) begin
        $fatal(1, "malformed vector line cycle=%0d fields=%0d", cycle, rc);
      end
    end
    $fclose(fd);
    $display("A2_K2_ADAPTER_LOCKSTEP_PASS cycles=%0d", cycle);
    $finish;
  end
endmodule
