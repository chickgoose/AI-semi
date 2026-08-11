`timescale 1ns/1ps

module a6_ef_cycle_lockstep_tb;
  localparam int NUM_SOURCES = 16;
  localparam int MAX_BATCH = 16;
  localparam int ADDRESS_WIDTH = 4;
  localparam int COUNT_WIDTH = 5;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic batch_valid;
  logic batch_ready;
  logic [COUNT_WIDTH-1:0] batch_count;
  logic [MAX_BATCH*ADDRESS_WIDTH-1:0] batch_sources;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  logic event_valid;
  logic event_ready;
  logic [ADDRESS_WIDTH-1:0] event_address;
  logic encode_error;
  logic decode_error;
  logic encoded_ef;

  integer oracle_file;
  integer scan_count;
  integer cycle;
  integer transaction_index;
  integer expected_address [0:127];
  integer expected_occurrence [0:127];
  integer expected_head;
  integer expected_tail;
  integer actual_accepted;
  integer actual_link_count;
  integer actual_link_data;
  integer actual_retired;
  integer actual_retired_address;
  integer actual_retired_latency;
  integer row_cycle;
  integer row_accepted;
  integer row_link_count;
  integer row_link_data;
  integer row_decoded_valid;
  integer row_decoded_address;
  integer row_retired;
  integer row_retired_address;
  integer row_retired_latency;
  integer source;
  reg [1023:0] header;

  always #1 clk = ~clk;

  a6_ef_batch_encoder encoder (
    .clk(clk), .rst_n(rst_n),
    .batch_valid(batch_valid), .batch_ready(batch_ready),
    .batch_count(batch_count), .batch_sources(batch_sources),
    .link_count(link_count), .link_data(link_data), .link_ready(link_ready),
    .encode_error(encode_error), .encoded_ef_observe(encoded_ef)
  );

  a6_ef_batch_decoder decoder (
    .clk(clk), .rst_n(rst_n),
    .link_count(link_count), .link_data(link_data), .link_ready(link_ready),
    .event_valid(event_valid), .event_ready(event_ready),
    .event_address(event_address), .decode_error(decode_error)
  );

  task automatic drive_transaction(input integer index);
    integer local_source;
    begin
      batch_sources = '0;
      case (index)
        0, 1, 2: begin
          batch_count = 16;
          for (local_source = 0; local_source < 16;
               local_source = local_source + 1)
            batch_sources[local_source*ADDRESS_WIDTH +: ADDRESS_WIDTH] =
              local_source[ADDRESS_WIDTH-1:0];
          batch_valid = 1'b1;
        end
        3: begin
          batch_count = 3;
          batch_sources[0 +: ADDRESS_WIDTH] = 1;
          batch_sources[4 +: ADDRESS_WIDTH] = 7;
          batch_sources[8 +: ADDRESS_WIDTH] = 14;
          batch_valid = 1'b1;
        end
        default: begin
          batch_count = '0;
          batch_valid = 1'b0;
        end
      endcase
    end
  endtask

  task automatic append_accepted_provenance(input integer index);
    integer local_source;
    begin
      if (index < 3) begin
        for (local_source = 0; local_source < 16;
             local_source = local_source + 1) begin
          expected_address[expected_tail] = local_source;
          expected_occurrence[expected_tail] = index;
          expected_tail = expected_tail + 1;
        end
      end else begin
        expected_address[expected_tail] = 1;
        expected_occurrence[expected_tail] = 3;
        expected_tail = expected_tail + 1;
        expected_address[expected_tail] = 7;
        expected_occurrence[expected_tail] = 3;
        expected_tail = expected_tail + 1;
        expected_address[expected_tail] = 14;
        expected_occurrence[expected_tail] = 3;
        expected_tail = expected_tail + 1;
      end
    end
  endtask

  always @(posedge clk) begin
    if (rst_n) begin
      actual_accepted = batch_valid && batch_ready;
      actual_link_count = link_ready ? link_count : 0;
      actual_link_data = (link_ready && link_count != 0) ? link_data : 0;
      actual_retired = event_valid && event_ready;
      actual_retired_address = actual_retired ? event_address : 0;
      actual_retired_latency = 0;
      if (actual_accepted)
        append_accepted_provenance(transaction_index);
      if (actual_retired) begin
        if (expected_head >= expected_tail)
          $fatal(1, "cycle oracle saw phantom retirement cycle=%0d", cycle);
        if (event_address !== expected_address[expected_head][ADDRESS_WIDTH-1:0])
          $fatal(1, "cycle oracle order mismatch cycle=%0d", cycle);
        actual_retired_latency = cycle - expected_occurrence[expected_head];
        expected_head = expected_head + 1;
      end
    end
  end

  always @(negedge clk) begin
    if (rst_n) begin
      scan_count = $fscanf(
        oracle_file, "%d %d %d %d %d %d %d %d %d\n",
        row_cycle, row_accepted, row_link_count, row_link_data,
        row_decoded_valid, row_decoded_address, row_retired,
        row_retired_address, row_retired_latency
      );
      if (scan_count != 9)
        $fatal(1, "cycle oracle ended or malformed at cycle=%0d", cycle);
      if (row_cycle != cycle || row_accepted != actual_accepted ||
          row_link_count != actual_link_count ||
          row_link_data != actual_link_data ||
          row_decoded_valid != event_valid ||
          (event_valid && row_decoded_address != event_address) ||
          row_retired != actual_retired ||
          row_retired_address != actual_retired_address ||
          row_retired_latency != actual_retired_latency)
        $fatal(1,
          "cycle mismatch c=%0d acc=%0d/%0d link=%0d,%0d/%0d,%0d vis=%0d,%0d/%0d,%0d ret=%0d,%0d,%0d/%0d,%0d,%0d",
          cycle, actual_accepted, row_accepted,
          actual_link_count, actual_link_data, row_link_count, row_link_data,
          event_valid, event_address, row_decoded_valid, row_decoded_address,
          actual_retired, actual_retired_address, actual_retired_latency,
          row_retired, row_retired_address, row_retired_latency);
      if (encode_error || decode_error)
        $fatal(1, "cycle oracle codec error cycle=%0d", cycle);

      if (actual_accepted) begin
        transaction_index = transaction_index + 1;
        drive_transaction(transaction_index);
      end
      cycle = cycle + 1;
      event_ready = (cycle >= 45);
      if ($feof(oracle_file)) begin
        if (expected_head != expected_tail)
          $fatal(1, "cycle oracle stopped with pending provenance");
        $display("A6_EF_CYCLE_LOCKSTEP_PASS cycles=%0d retired=%0d",
                 cycle, expected_head);
        $finish;
      end
    end
  end

  initial begin
    batch_valid = 1'b0;
    batch_count = '0;
    batch_sources = '0;
    event_ready = 1'b0;
    cycle = 0;
    transaction_index = 0;
    expected_head = 0;
    expected_tail = 0;
    actual_accepted = 0;
    actual_link_count = 0;
    actual_link_data = 0;
    actual_retired = 0;
    actual_retired_address = 0;
    actual_retired_latency = 0;
    oracle_file = $fopen(
      "rtl/candidates/a6_elias_fano_monotone_link/a6_ef_cycle_oracle.tsv", "r");
    if (oracle_file == 0)
      $fatal(1, "cannot open A6 EF cycle oracle");
    scan_count = $fgets(header, oracle_file);
    if (scan_count == 0)
      $fatal(1, "cannot read A6 EF cycle oracle header");

    repeat (4) @(posedge clk);
    @(negedge clk);
    drive_transaction(0);
    #0.1;
    rst_n = 1'b1;
  end
endmodule
