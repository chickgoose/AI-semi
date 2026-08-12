#!/usr/bin/env python3
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("checker", HERE / "check_contract.py")
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(checker)
FOVEA = Path("/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures/aer_tx16_trad_rowcol_fovea.v")
A7 = Path("/home/chickgoose/projects/a7")


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FOVEA.is_file():
            raise RuntimeError(f"canonical external FOVEA fixture unavailable: {FOVEA}")

    def test_canonical_external_sources_and_seam_pass(self):
        proc = subprocess.run(
            ["python3", str(HERE / "check_contract.py"), "--fovea", str(FOVEA),
             "--a7-repo", str(A7)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"ok": true', proc.stdout)

    def test_fovea_byte_mutation_fails(self):
        data = FOVEA.read_bytes().replace(b"parameter WEIGHT = 5", b"parameter WEIGHT = 4", 1)
        with self.assertRaisesRegex(checker.ContractError, "SHA mismatch"):
            checker.check_fovea(data)

    def test_seam_queue_state_fails(self):
        data = (HERE / "fovea_a7_zero_state_seam.sv").read_bytes() + b"\nalways_ff @(posedge ref_clk_i) ;\n"
        with self.assertRaisesRegex(checker.ContractError, "forbidden seam feature"):
            checker.check_seam(data)

    def test_seam_ack_mask_mutation_fails(self):
        data = (HERE / "fovea_a7_zero_state_seam.sv").read_bytes().replace(
            b"source_valid_i & ~current_result_mask", b"source_valid_i", 1)
        with self.assertRaisesRegex(checker.ContractError, "completion masking"):
            checker.check_seam(data)

    def test_seam_ready_identity_mutation_fails(self):
        data = (HERE / "fovea_a7_zero_state_seam.sv").read_bytes().replace(
            b"source_valid_i & current_result_mask", b"current_result_mask", 1)
        with self.assertRaisesRegex(checker.ContractError, "ACK identity"):
            checker.check_seam(data)

    def test_seam_address_reconstruction_fails(self):
        data = (HERE / "fovea_a7_zero_state_seam.sv").read_bytes().replace(
            b".event_addr_i(fovea_addr)", b".event_addr_i({fovea_addr[1:0], fovea_addr[3:2]})", 1)
        with self.assertRaisesRegex(checker.ContractError, "direct address identity"):
            checker.check_seam(data)

    def test_failure_receipt_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.v"
            bad.write_text("module wrong; endmodule\n")
            proc = subprocess.run(
                ["python3", str(HERE / "check_contract.py"), "--fovea", str(bad),
                 "--a7-repo", str(A7)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('"ok": false', proc.stdout)
        self.assertIn("FOVEA SHA mismatch", proc.stdout)


if __name__ == "__main__":
    unittest.main()
