`timescale 1ns/1ps

module a4_w4_common_tb;
  localparam int SOURCES = 16;
  localparam int MAX_CREDITS = 16384;

  logic clk = 1'b0;
  logic rst_n;
  logic [15:0] moving_valid, moving_ready;
  logic [15:0] fixed_valid, fixed_ready;
  logic moving_retire_valid, fixed_retire_valid;
  logic [3:0] moving_retire_address, fixed_retire_address;
  logic [31:0] moving_raw_event, fixed_raw_event;

  logic [63:0] moving_credit [0:SOURCES-1][0:MAX_CREDITS-1];
  logic [63:0] fixed_credit [0:SOURCES-1][0:MAX_CREDITS-1];
  integer moving_latency [0:SOURCES-1][0:MAX_CREDITS-1];
  integer fixed_latency [0:SOURCES-1][0:MAX_CREDITS-1];
  integer moving_delivery_cycle [0:SOURCES-1][0:MAX_CREDITS-1];
  integer fixed_delivery_cycle [0:SOURCES-1][0:MAX_CREDITS-1];
  integer moving_write [0:SOURCES-1], moving_read [0:SOURCES-1];
  integer fixed_write [0:SOURCES-1], fixed_read [0:SOURCES-1];
  integer stim_cycles, expected_rows, offered;
  integer expected_m_overrun, expected_m_accepted, expected_m_retired;
  integer expected_m_fixed, expected_m_sum, expected_m_max;
  integer expected_f_overrun, expected_f_accepted, expected_f_retired;
  integer expected_f_fixed, expected_f_sum, expected_f_max;
  integer moving_accepted, moving_retired, moving_fixed, moving_max, moving_sum;
  integer fixed_accepted, fixed_retired, fixed_fixed, fixed_max, fixed_sum;

  always #5 clk <= ~clk;

  a4_w4_zero_state_adapter #(.MAX_ADVANCE(2)) moving (
    .clk, .rst_n, .source_valid(moving_valid), .source_ready(moving_ready),
    .retire_valid(moving_retire_valid), .retire_ready(1'b1),
    .retire_address(moving_retire_address), .raw_retire_event(moving_raw_event)
  );
  a4_w4_zero_state_adapter #(.MAX_ADVANCE(1)) fixed (
    .clk, .rst_n, .source_valid(fixed_valid), .source_ready(fixed_ready),
    .retire_valid(fixed_retire_valid), .retire_ready(1'b1),
    .retire_address(fixed_retire_address), .raw_retire_event(fixed_raw_event)
  );

  initial begin
    string vector_path;
    string magic;
    integer vectors, scan_status;
    integer stim_cycle, rst_value, rows;
    logic [15:0] moving_valid_value, fixed_valid_value;
    logic [63:0] moving_token [0:SOURCES-1];
    logic [63:0] fixed_token [0:SOURCES-1];
    logic [15:0] expected_m_ready, expected_f_ready;
    integer expected_m_rvalid, expected_f_rvalid;
    logic [3:0] expected_m_source, expected_f_source;
    logic [63:0] token;
    integer latency;

    rst_n = 1'b0;
    moving_valid = '0;
    fixed_valid = '0;
    rows = 0;
    moving_accepted = 0;
    moving_retired = 0;
    moving_fixed = 0;
    moving_sum = 0;
    moving_max = 0;
    fixed_accepted = 0;
    fixed_retired = 0;
    fixed_fixed = 0;
    fixed_sum = 0;
    fixed_max = 0;
    for (int source = 0; source < SOURCES; source++) begin
      moving_write[source] = 0;
      moving_read[source] = 0;
      fixed_write[source] = 0;
      fixed_read[source] = 0;
    end

    if (!$value$plusargs("VECTORS=%s", vector_path))
      $fatal(1, "W4 missing +VECTORS");
    vectors = $fopen(vector_path, "r");
    if (vectors == 0)
      $fatal(1, "W4 cannot open vectors: %s", vector_path);
    scan_status = $fscanf(vectors,
      "%s %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
      magic, stim_cycles, expected_rows, offered,
      expected_m_overrun, expected_m_accepted, expected_m_retired,
      expected_m_fixed, expected_m_sum, expected_m_max,
      expected_f_overrun, expected_f_accepted, expected_f_retired,
      expected_f_fixed, expected_f_sum, expected_f_max);
    if (scan_status != 16 || magic != "W4V1")
      $fatal(1, "W4 malformed vector header status=%0d magic=%s", scan_status, magic);

    while (!$feof(vectors)) begin
      scan_status = $fscanf(vectors, "%d %d %h", stim_cycle, rst_value, moving_valid_value);
      if (scan_status != 3)
        break;
      for (int source = 0; source < SOURCES; source++) begin
        scan_status = $fscanf(vectors, "%h", moving_token[source]);
        if (scan_status != 1) $fatal(1, "W4 truncated moving token row=%0d", rows);
      end
      scan_status = $fscanf(vectors, "%h %d %h %h",
        expected_m_ready, expected_m_rvalid, expected_m_source, fixed_valid_value);
      if (scan_status != 4) $fatal(1, "W4 truncated moving expectation row=%0d", rows);
      for (int source = 0; source < SOURCES; source++) begin
        scan_status = $fscanf(vectors, "%h", fixed_token[source]);
        if (scan_status != 1) $fatal(1, "W4 truncated fixed token row=%0d", rows);
      end
      scan_status = $fscanf(vectors, "%h %d %h\n",
        expected_f_ready, expected_f_rvalid, expected_f_source);
      if (scan_status != 3) $fatal(1, "W4 truncated fixed expectation row=%0d", rows);

      @(negedge clk);
      rst_n = (rst_value != 0);
      moving_valid = moving_valid_value;
      fixed_valid = fixed_valid_value;
      #1;
      if (moving_ready !== expected_m_ready)
        $fatal(1, "W4 moving ready lockstep row=%0d got=%h expected=%h", rows, moving_ready, expected_m_ready);
      if (fixed_ready !== expected_f_ready)
        $fatal(1, "W4 fixed ready lockstep row=%0d got=%h expected=%h", rows, fixed_ready, expected_f_ready);
      if (moving_retire_valid !== expected_m_rvalid[0])
        $fatal(1, "W4 moving retire-valid lockstep row=%0d", rows);
      if (fixed_retire_valid !== expected_f_rvalid[0])
        $fatal(1, "W4 fixed retire-valid lockstep row=%0d", rows);
      if (expected_m_rvalid != 0 && moving_retire_address !== expected_m_source)
        $fatal(1, "W4 moving retire-source lockstep row=%0d", rows);
      if (expected_f_rvalid != 0 && fixed_retire_address !== expected_f_source)
        $fatal(1, "W4 fixed retire-source lockstep row=%0d", rows);
      if (!rst_n && (moving_ready != 0 || fixed_ready != 0 ||
                     moving_retire_valid || fixed_retire_valid))
        $fatal(1, "W4 output not quiet during reset row=%0d", rows);

      for (int source = 0; source < SOURCES; source++) begin
        if (moving_ready[source]) begin
          if (!moving_valid[source] || moving_token[source] == 0)
            $fatal(1, "W4 moving acceptance without credit row=%0d source=%0d", rows, source);
          if (moving_write[source] >= MAX_CREDITS)
            $fatal(1, "W4 moving credit overflow source=%0d", source);
          moving_credit[source][moving_write[source]] = moving_token[source];
          moving_write[source] = moving_write[source] + 1;
        end
        if (fixed_ready[source]) begin
          if (!fixed_valid[source] || fixed_token[source] == 0)
            $fatal(1, "W4 fixed acceptance without credit row=%0d source=%0d", rows, source);
          if (fixed_write[source] >= MAX_CREDITS)
            $fatal(1, "W4 fixed credit overflow source=%0d", source);
          fixed_credit[source][fixed_write[source]] = fixed_token[source];
          fixed_write[source] = fixed_write[source] + 1;
        end
      end

      if (moving_retire_valid) begin
        if (moving_raw_event !== {28'b0, moving_retire_address})
          $fatal(1, "W4 moving non-address payload row=%0d raw=%h source=%h", rows, moving_raw_event, moving_retire_address);
        if (moving_read[moving_retire_address] >= moving_write[moving_retire_address])
          $fatal(1, "W4 moving phantom/duplicate row=%0d source=%0d", rows, moving_retire_address);
        token = moving_credit[moving_retire_address][moving_read[moving_retire_address]];
        latency = stim_cycle - integer'((token >> 32) - 1) + 1;
        moving_latency[moving_retire_address][moving_read[moving_retire_address]] = latency;
        moving_delivery_cycle[moving_retire_address][moving_read[moving_retire_address]] = stim_cycle;
        moving_read[moving_retire_address] = moving_read[moving_retire_address] + 1;
        if (latency <= 0) $fatal(1, "W4 moving invalid occurrence latency row=%0d", rows);
      end
      if (fixed_retire_valid) begin
        if (fixed_raw_event !== {28'b0, fixed_retire_address})
          $fatal(1, "W4 fixed non-address payload row=%0d raw=%h source=%h", rows, fixed_raw_event, fixed_retire_address);
        if (fixed_read[fixed_retire_address] >= fixed_write[fixed_retire_address])
          $fatal(1, "W4 fixed phantom/duplicate row=%0d source=%0d", rows, fixed_retire_address);
        token = fixed_credit[fixed_retire_address][fixed_read[fixed_retire_address]];
        latency = stim_cycle - integer'((token >> 32) - 1) + 1;
        fixed_latency[fixed_retire_address][fixed_read[fixed_retire_address]] = latency;
        fixed_delivery_cycle[fixed_retire_address][fixed_read[fixed_retire_address]] = stim_cycle;
        fixed_read[fixed_retire_address] = fixed_read[fixed_retire_address] + 1;
        if (latency <= 0) $fatal(1, "W4 fixed invalid occurrence latency row=%0d", rows);
      end

      @(posedge clk);
      #1;
      rows = rows + 1;
    end
    $fclose(vectors);

    if (rows != expected_rows) $fatal(1, "W4 row count got=%0d expected=%0d", rows, expected_rows);
    moving_accepted = 0;
    moving_retired = 0;
    moving_fixed = 0;
    moving_sum = 0;
    moving_max = 0;
    fixed_accepted = 0;
    fixed_retired = 0;
    fixed_fixed = 0;
    fixed_sum = 0;
    fixed_max = 0;
    for (int source = 0; source < SOURCES; source++) begin
      moving_accepted = moving_accepted + moving_write[source];
      moving_retired = moving_retired + moving_read[source];
      fixed_accepted = fixed_accepted + fixed_write[source];
      fixed_retired = fixed_retired + fixed_read[source];
      for (int credit = 0; credit < moving_read[source]; credit++) begin
        moving_sum = moving_sum + moving_latency[source][credit];
        if (moving_latency[source][credit] > moving_max)
          moving_max = moving_latency[source][credit];
        if (moving_delivery_cycle[source][credit] >= 0 &&
            moving_delivery_cycle[source][credit] < stim_cycles)
          moving_fixed = moving_fixed + 1;
      end
      for (int credit = 0; credit < fixed_read[source]; credit++) begin
        fixed_sum = fixed_sum + fixed_latency[source][credit];
        if (fixed_latency[source][credit] > fixed_max)
          fixed_max = fixed_latency[source][credit];
        if (fixed_delivery_cycle[source][credit] >= 0 &&
            fixed_delivery_cycle[source][credit] < stim_cycles)
          fixed_fixed = fixed_fixed + 1;
      end
    end
    if (moving_accepted != moving_retired || fixed_accepted != fixed_retired)
      $fatal(1, "W4 accepted-event conservation failed");
    for (int source = 0; source < SOURCES; source++) begin
      if (moving_read[source] != moving_write[source])
        $fatal(1, "W4 moving undrained credit source=%0d", source);
      if (fixed_read[source] != fixed_write[source])
        $fatal(1, "W4 fixed undrained credit source=%0d", source);
    end
    $display("W4_A4_ALWAYS_READY_GENERATOR_V4_TRACE_LOCKSTEP_PASS rows=%0d offered=%0d moving=%0d,%0d,%0d,%0d,%0d,%0d fixed=%0d,%0d,%0d,%0d,%0d,%0d expected=%0d,%0d,%0d,%0d,%0d/%0d,%0d,%0d,%0d,%0d",
      rows, offered,
      moving_accepted, moving_retired, moving_fixed, moving_sum, moving_max, expected_m_overrun,
      fixed_accepted, fixed_retired, fixed_fixed, fixed_sum, fixed_max, expected_f_overrun,
      expected_m_accepted, expected_m_retired, expected_m_fixed, expected_m_sum, expected_m_max,
      expected_f_accepted, expected_f_retired, expected_f_fixed, expected_f_sum, expected_f_max);
    $finish;
  end
endmodule
