`timescale 1ns/1ps

module aer_improved_tb;
    localparam int NUM_SOURCES = 3;
    localparam int ADDR_WIDTH  = 8;
    localparam int FIFO_DEPTH = 2;
    localparam int EVENTS_PER_SOURCE = 18;
    localparam int SOURCE_WIDTH = $clog2(NUM_SOURCES);
    localparam int MAX_EVENTS = 128;

    logic clk;
    logic rst_n;

    logic [NUM_SOURCES-1:0] in_valid;
    logic [NUM_SOURCES-1:0] in_ready;
    logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES];
    logic out_valid;
    logic out_ready;
    logic [ADDR_WIDTH-1:0] out_addr;
    logic [SOURCE_WIDTH-1:0] out_src;

    logic fifo_in_valid;
    logic fifo_in_ready;
    logic [ADDR_WIDTH-1:0] fifo_in_data;
    logic fifo_out_valid;
    logic fifo_out_ready;
    logic [ADDR_WIDTH-1:0] fifo_out_data;
    logic [$clog2(FIFO_DEPTH+1)-1:0] fifo_occupancy;

    logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][MAX_EVENTS];
    integer head [NUM_SOURCES];
    integer tail [NUM_SOURCES];
    integer service_count [NUM_SOURCES];
    integer accepted_count;
    integer emitted_count;
    integer error_count;
    integer source;
    integer sent [NUM_SOURCES];
    integer minimum_service;
    integer maximum_service;
    logic check_fairness;
    logic previous_stalled;
    logic [ADDR_WIDTH-1:0] stalled_addr;
    logic [SOURCE_WIDTH-1:0] stalled_src;

    aer_dut #(
        .NUM_SOURCES (NUM_SOURCES),
        .ADDR_WIDTH  (ADDR_WIDTH),
        .FIFO_DEPTH  (FIFO_DEPTH)
    ) u_dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .in_valid  (in_valid),
        .in_ready  (in_ready),
        .in_addr   (in_addr),
        .out_valid (out_valid),
        .out_ready (out_ready),
        .out_addr  (out_addr),
        .out_src   (out_src)
    );

    aer_sync_fifo #(
        .DATA_WIDTH (ADDR_WIDTH),
        .DEPTH      (FIFO_DEPTH)
    ) u_fifo_unit (
        .clk_i       (clk),
        .rst_ni      (rst_n),
        .in_valid_i  (fifo_in_valid),
        .in_ready_o  (fifo_in_ready),
        .in_data_i   (fifo_in_data),
        .out_valid_o (fifo_out_valid),
        .out_ready_i (fifo_out_ready),
        .out_data_o  (fifo_out_data),
        .occupancy_o (fifo_occupancy)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    function automatic logic [ADDR_WIDTH-1:0] make_event(
        input integer source_id,
        input integer event_sequence
    );
        make_event = ADDR_WIDTH'((source_id << 5) | event_sequence);
    endfunction

    task automatic report_error(input string message);
        begin
            $error("%s", message);
            error_count = error_count + 1;
        end
    endtask

    task automatic clear_inputs;
        integer i;
        begin
            in_valid = '0;
            out_ready = 1'b0;
            fifo_in_valid = 1'b0;
            fifo_in_data = '0;
            fifo_out_ready = 1'b0;
            check_fairness = 1'b0;
            for (i = 0; i < NUM_SOURCES; i = i + 1)
                in_addr[i] = '0;
        end
    endtask

    task automatic reset_design;
        begin
            @(negedge clk);
            clear_inputs();
            rst_n = 1'b0;
            repeat (3) @(posedge clk);
            @(negedge clk);
            rst_n = 1'b1;
        end
    endtask

    task automatic push_fifo(input logic [ADDR_WIDTH-1:0] data);
        begin
            @(negedge clk);
            fifo_in_valid = 1'b1;
            fifo_in_data = data;
            fifo_out_ready = 1'b0;
            #1;
            if (!fifo_in_ready)
                report_error("FIFO unexpectedly rejected a fill push");
            @(posedge clk);
            #1;
            fifo_in_valid = 1'b0;
        end
    endtask

    task automatic test_fifo_full_replacement;
        begin
            $display("A2_TEST FIFO full simultaneous pop/push");
            push_fifo(8'h11);
            push_fifo(8'h22);

            if (fifo_occupancy != FIFO_DEPTH || fifo_in_ready)
                report_error("FIFO did not enter the expected full state");

            @(negedge clk);
            fifo_in_valid = 1'b1;
            fifo_in_data = 8'h33;
            fifo_out_ready = 1'b1;
            #1;
            if (!fifo_in_ready || !fifo_out_valid || fifo_out_data != 8'h11)
                report_error("FIFO full replacement handshake is incorrect");
            @(posedge clk);
            #1;
            fifo_in_valid = 1'b0;
            if (fifo_occupancy != FIFO_DEPTH || fifo_out_data != 8'h22)
                report_error("FIFO replacement changed count/order");

            @(posedge clk);
            if (!fifo_out_valid || fifo_out_data != 8'h22)
                report_error("FIFO lost or reordered the second event");
            #1;
            if (fifo_occupancy != 1 || fifo_out_data != 8'h33)
                report_error("FIFO replacement event was not retained");

            @(posedge clk);
            if (!fifo_out_valid || fifo_out_data != 8'h33)
                report_error("FIFO replacement event was duplicated or lost");
            #1;
            fifo_out_ready = 1'b0;
            if (fifo_occupancy != 0 || fifo_out_valid)
                report_error("FIFO did not become empty after the expected pops");
        end
    endtask

    task automatic test_grant_lock;
        logic [ADDR_WIDTH-1:0] held_addr;
        logic [SOURCE_WIDTH-1:0] held_src;
        integer stall_cycle;
        begin
            $display("A2_TEST backpressure grant lock");
            reset_design();

            @(negedge clk);
            in_valid[2] = 1'b1;
            in_addr[2] = 8'hA2;
            @(posedge clk);
            @(negedge clk);
            in_valid[2] = 1'b0;
            in_valid[0] = 1'b1;
            in_addr[0] = 8'hB0;
            #1;
            if (!out_valid || out_src != 2 || out_addr != 8'hA2)
                report_error("Unexpected initial grant before stall");
            held_addr = out_addr;
            held_src = out_src;

            for (stall_cycle = 0; stall_cycle < 4; stall_cycle = stall_cycle + 1) begin
                @(posedge clk);
                #1;
                if (!out_valid || out_addr != held_addr || out_src != held_src)
                    report_error("Grant/payload changed while output was stalled");
                @(negedge clk);
                if (in_ready[0])
                    in_valid[0] = 1'b0;
            end

            out_ready = 1'b1;
            @(posedge clk);
            #1;
            if (!out_valid || out_src != 0 || out_addr != 8'hB0)
                report_error("Round-robin did not advance after releasing stall");
            @(posedge clk);
            #1;
            out_ready = 1'b0;
            if (out_valid)
                report_error("Unexpected duplicate output after grant-lock test");
        end
    endtask

    task automatic test_saturated_fairness;
        integer active_sources;
        integer i;
        begin
            $display("A2_TEST three-source saturated fairness");
            reset_design();
            for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                sent[i] = 0;
                in_valid[i] = 1'b1;
                in_addr[i] = make_event(i, 0);
            end
            out_ready = 1'b1;
            check_fairness = 1'b1;
            active_sources = NUM_SOURCES;

            while (active_sources != 0) begin
                @(posedge clk);
                for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                    if (in_valid[i] && in_ready[i])
                        sent[i] = sent[i] + 1;
                end
                @(negedge clk);
                active_sources = 0;
                for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                    if (sent[i] < EVENTS_PER_SOURCE) begin
                        in_valid[i] = 1'b1;
                        in_addr[i] = make_event(i, sent[i]);
                        active_sources = active_sources + 1;
                    end else begin
                        in_valid[i] = 1'b0;
                    end
                end
            end

            while (emitted_count < accepted_count)
                @(posedge clk);
            @(negedge clk);
            check_fairness = 1'b0;
            out_ready = 1'b0;

            for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                if (sent[i] != EVENTS_PER_SOURCE ||
                    service_count[i] != EVENTS_PER_SOURCE)
                    report_error("A source was starved or lost events");
            end
            if (accepted_count != NUM_SOURCES * EVENTS_PER_SOURCE ||
                emitted_count != accepted_count)
                report_error("Saturated test count mismatch");
        end
    endtask

    always @(posedge clk or negedge rst_n) begin
        integer i;
        if (!rst_n) begin
            accepted_count = 0;
            emitted_count = 0;
            previous_stalled = 1'b0;
            stalled_addr = '0;
            stalled_src = '0;
            for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                head[i] = 0;
                tail[i] = 0;
                service_count[i] = 0;
            end
        end else begin
            if (previous_stalled &&
                (!out_valid || out_addr !== stalled_addr || out_src !== stalled_src))
                report_error("Monitor detected output changes during stall");
            previous_stalled = out_valid && !out_ready;
            if (out_valid && !out_ready) begin
                stalled_addr = out_addr;
                stalled_src = out_src;
            end

            for (i = 0; i < NUM_SOURCES; i = i + 1) begin
                if (in_valid[i] && in_ready[i]) begin
                    expected[i][tail[i]] = in_addr[i];
                    tail[i] = tail[i] + 1;
                    accepted_count = accepted_count + 1;
                end
            end

            if (out_valid && out_ready) begin
                if ($isunknown({out_src, out_addr}) || out_src >= NUM_SOURCES) begin
                    report_error("Unknown or illegal output payload");
                end else if (head[out_src] >= tail[out_src]) begin
                    report_error("Duplicate or unexpected output event");
                end else begin
                    if (out_addr !== expected[out_src][head[out_src]])
                        report_error("Output event was lost, reordered, or corrupted");
                    head[out_src] = head[out_src] + 1;
                    service_count[out_src] = service_count[out_src] + 1;
                    emitted_count = emitted_count + 1;
                end

                if (check_fairness) begin
                    minimum_service = service_count[0];
                    maximum_service = service_count[0];
                    for (i = 1; i < NUM_SOURCES; i = i + 1) begin
                        if (service_count[i] < minimum_service)
                            minimum_service = service_count[i];
                        if (service_count[i] > maximum_service)
                            maximum_service = service_count[i];
                    end
                    if ((maximum_service - minimum_service) > 1)
                        report_error("Saturated service skew exceeded one event");
                end
            end
        end
    end

    initial begin
        error_count = 0;
        rst_n = 1'b0;
        clear_inputs();
        repeat (3) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        test_fifo_full_replacement();
        test_grant_lock();
        test_saturated_fairness();

        if (error_count == 0) begin
            $display("A2_TEST_PASS all improved RTL checks");
            $finish;
        end else begin
            $fatal(1, "A2_TEST_FAIL errors=%0d", error_count);
        end
    end

endmodule
