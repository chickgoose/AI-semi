import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_address_derivation as lint


class AddressDerivationLintTest(unittest.TestCase):
    def test_rejects_pending_event_reconstruction(self):
        bad = "bench.retire_event[0] = bench.source_event[native_addr];"
        with self.assertRaisesRegex(lint.DerivationError, "source_event"):
            lint.check_native(bad)

    def test_rejects_free_metadata_even_without_source_event_name(self):
        bad = "bench.retire_event[0] = free_metadata;"
        with self.assertRaisesRegex(lint.DerivationError, "native_addr"):
            lint.check_native(bad)

    def test_rejects_cluster2_source_not_derived_from_row_column(self):
        bad = """
        assign cluster2_req = bench.source_valid & ~cluster2_current_result_mask;
        bench.source_ready = bench.source_valid & cluster2_current_result_mask;
        raw_cluster2_dut dut();
        AER_CLUSTER2_RAW_OBSERVATION_BEGIN
        cluster2_source = free_metadata;
        bench.retire_event[cluster2_col] = ADDR_WIDTH'(cluster2_source);
        bench.retire_event[4 + cluster2_col] = ADDR_WIDTH'(cluster2_source);
        AER_CLUSTER2_RAW_OBSERVATION_END
        """
        marked = f"AER_CLUSTER2_BINDING_BEGIN\n{bad}\nAER_CLUSTER2_BINDING_END"
        with self.assertRaisesRegex(lint.DerivationError, "row/column"):
            lint.check_cluster2_binding(marked)

    def test_rejects_cluster2_monitor_that_reads_metadata(self):
        bad = """
        AER_CLUSTER2_BINDING_BEGIN
        assign cluster2_req = bench.source_valid & ~cluster2_current_result_mask;
        raw_cluster2_dut dut();
        bench.retire_event[0] = bench.source_event[0];
        AER_CLUSTER2_BINDING_END
        """
        with self.assertRaisesRegex(lint.DerivationError, "source_event"):
            lint.check_cluster2_binding(bad)

    def test_rejects_cluster2_monitor_with_adapter_state(self):
        bad = """
        AER_CLUSTER2_BINDING_BEGIN
        assign cluster2_req = bench.source_valid & ~cluster2_current_result_mask;
        raw_cluster2_dut dut();
        always_ff @(posedge bench.clk) stored_grant <= cluster2_row0;
        AER_CLUSTER2_BINDING_END
        """
        with self.assertRaisesRegex(lint.DerivationError, "state/control"):
            lint.check_cluster2_binding(bad)

    def test_rejects_unmasked_level_request_driver(self):
        bad = """
        AER_CLUSTER2_BINDING_BEGIN
        assign cluster2_req = bench.source_valid;
        raw_cluster2_dut dut();
        AER_CLUSTER2_BINDING_END
        """
        with self.assertRaisesRegex(lint.DerivationError, "current-result"):
            lint.check_cluster2_binding(bad)

    def test_rejects_pending_gated_raw_retirement(self):
        bad = """
        AER_CLUSTER2_BINDING_BEGIN
        assign cluster2_req = bench.source_valid & ~cluster2_current_result_mask;
        bench.source_ready = bench.source_valid & cluster2_current_result_mask;
        raw_cluster2_dut dut();
        AER_CLUSTER2_RAW_OBSERVATION_BEGIN
        if (bench.source_valid[cluster2_source]) begin
          cluster2_source = (integer'(cluster2_row0) * 4) + cluster2_col;
          cluster2_source = (integer'(cluster2_row1) * 4) + cluster2_col;
          bench.retire_event[cluster2_col] = ADDR_WIDTH'(cluster2_source);
          bench.retire_event[4 + cluster2_col] = ADDR_WIDTH'(cluster2_source);
        end
        AER_CLUSTER2_RAW_OBSERVATION_END
        AER_CLUSTER2_BINDING_END
        """
        with self.assertRaisesRegex(lint.DerivationError, "raw retirement"):
            lint.check_cluster2_binding(bad)

    def test_rejects_inline_cluster2_protocol_in_common_tb(self):
        bad = """
        aer_ganghee_cluster2_binding candidate(bench);
        raw_cluster2_dut dut();
        """
        with self.assertRaisesRegex(lint.DerivationError, "inline"):
            lint.check_cluster2_common_tb(bad)


if __name__ == "__main__":
    unittest.main()
