`timescale 1ns/1ps

module mc_wtb_epoch_route_interlock_tb;
  logic clk_i = 0;
  logic rst_i = 1;
  logic request_valid_i = 0;
  logic request_ready_o;
  logic [1:0] requested_route_i = 0;
  logic [7:0] requested_epoch_i = 0;
  logic pose_reliable_i = 1;
  logic profile_authorized_i = 1;
  logic ingress_valid_i = 0;
  logic ingress_ready_o;
  logic routed_ingress_valid_o;
  logic routed_ingress_ready_i = 1;
  logic transport_empty_i = 1;
  logic route_adapters_empty_i = 1;
  logic transport_healthy_i = 1;
  logic [1:0] active_route_o;
  logic [7:0] active_epoch_o;
  logic [2:0] route_enable_o;
  logic epoch_commit_o;
  logic transition_busy_o;
  logic protocol_error_o;

  mc_wtb_epoch_route_interlock #(.EPOCH_W(8)) dut (.*);
  always #5 clk_i = ~clk_i;

  task automatic request(input logic [1:0] route, input logic [7:0] epoch);
    begin
      while (!request_ready_o) @(posedge clk_i);
      @(negedge clk_i);
      requested_route_i = route;
      requested_epoch_i = epoch;
      request_valid_i = 1;
      @(posedge clk_i); #1;
      request_valid_i = 0;
    end
  endtask

  initial begin
    request_valid_i = 1;
    ingress_valid_i = 1;
    repeat (2) @(posedge clk_i);
    #1;
    if (request_ready_o || ingress_ready_o || routed_ingress_valid_o)
      $fatal(1, "reset exposed a ready/valid handshake");
    @(negedge clk_i); rst_i = 0;
    request_valid_i = 0;
    ingress_valid_i = 0;
    if (active_route_o != 0 || route_enable_o != 3'b001)
      $fatal(1, "reset route is not fail-safe bypass");

    // A request wins over simultaneous ingress and closes admission.
    ingress_valid_i = 1;
    transport_empty_i = 0;
    route_adapters_empty_i = 0;
    request(2'd1, 8'd1);
    if (!transition_busy_o || ingress_ready_o || routed_ingress_valid_o)
      $fatal(1, "transition did not freeze ingress");
    repeat (3) begin
      @(posedge clk_i); #1;
      if (active_route_o != 0) $fatal(1, "route changed before drain");
    end
    @(negedge clk_i); transport_empty_i = 1;
    @(posedge clk_i); #1;
    if (epoch_commit_o) $fatal(1, "commit ignored nonempty route adapter");
    @(negedge clk_i); route_adapters_empty_i = 1;
    @(posedge clk_i); #1;
    if (!epoch_commit_o || active_route_o != 1 || active_epoch_o != 1)
      $fatal(1, "clean drain did not commit sparse route");

    // A mid-route pose fault freezes ingress, drains old work, then bypasses.
    @(negedge clk_i); ingress_valid_i = 1; transport_empty_i = 0; pose_reliable_i = 0;
    @(posedge clk_i); #1;
    if (!transition_busy_o || ingress_ready_o || active_route_o != 1)
      $fatal(1, "pose fault did not freeze the acceptance-time route");
    @(negedge clk_i); ingress_valid_i = 0; transport_empty_i = 1;
    @(posedge clk_i); #1;
    if (!epoch_commit_o || active_route_o != 0 || active_epoch_o != 2)
      $fatal(1, "pose fault did not drain then commit bypass");
    repeat (2) begin
      @(posedge clk_i); #1;
      if (epoch_commit_o || active_epoch_o != 2 || request_ready_o)
        $fatal(1, "persistent pose fault created repeated bypass epochs");
    end
    @(negedge clk_i); pose_reliable_i = 1;
    @(posedge clk_i); #1;
    if (!request_ready_o) $fatal(1, "fault recovery did not reopen request channel");

    // An invalid route is sanitized to bypass and still uses drain/commit.
    ingress_valid_i = 0;
    request(2'd3, 8'd3);
    @(posedge clk_i); #1;
    if (!epoch_commit_o || active_route_o != 0 || !protocol_error_o)
      $fatal(1, "invalid route did not fail closed");

    // Clear the sticky protocol error only through a clean, idle reset.
    @(negedge clk_i); rst_i = 1;
    @(posedge clk_i); #1;
    @(negedge clk_i); rst_i = 0;
    if (protocol_error_o || active_route_o != 0)
      $fatal(1, "clean reset did not restore bypass");

    // Epoch zero is legal once after reset; replay is accepted only into ERROR hold.
    request(2'd0, 8'd0);
    @(posedge clk_i); #1;
    if (!epoch_commit_o || active_epoch_o != 0)
      $fatal(1, "first epoch zero did not commit");
    request(2'd0, 8'd0);
    if (!transition_busy_o || !protocol_error_o || epoch_commit_o)
      $fatal(1, "stale epoch request disappeared without fail-closed hold");
    @(negedge clk_i); rst_i = 1;
    @(posedge clk_i); #1;
    @(negedge clk_i); rst_i = 0;

    // A transport fault may not force-flush or change the acceptance-time route.
    transport_empty_i = 0;
    request(2'd2, 8'd1);
    @(negedge clk_i); transport_healthy_i = 0;
    @(posedge clk_i); #1;
    if (!transition_busy_o || active_route_o != 0 || !protocol_error_o)
      $fatal(1, "transport fault changed route or escaped ERROR hold");
    @(negedge clk_i); transport_healthy_i = 1; transport_empty_i = 1;
    @(posedge clk_i); #1;
    if (!transition_busy_o || epoch_commit_o || active_route_o != 0)
      $fatal(1, "ERROR hold forced a late commit");
    $display("MC_WTB_EPOCH_ROUTE_INTERLOCK_RTL_PASS");
    $finish;
  end
endmodule
