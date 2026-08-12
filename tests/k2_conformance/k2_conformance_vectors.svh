// Candidate-neutral protocol vectors.  Deliberately avoid arbitration-policy
// expectations: singleton identities are forced and pair order is learned from
// the offer, then checked through hold/accept/retire.
localparam logic [15:0] K2_VEC_SINGLE       = 16'h0020; // source 5
localparam logic [15:0] K2_VEC_PAIR         = 16'h2004; // sources 2,13
localparam logic [15:0] K2_VEC_REFILL_OLD   = 16'h4002; // sources 1,14
localparam logic [15:0] K2_VEC_REFILL_NEW   = 16'h0810; // sources 4,11
localparam logic [15:0] K2_VEC_IDENTITY     = 16'h8241; // sources 0,6,9,15
localparam logic [15:0] K2_VEC_RESET_SENTINEL = 16'h0080; // source 7
