from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "physical/k2_w3_common_activity/scale_vcd_timestamps.py"


def vcd(timestamps: tuple[int, ...] = (0, 10000, 20000),
        timescale: str = "$timescale 1ps $end\n") -> bytes:
    values = []
    for index, timestamp in enumerate(timestamps):
        values.append(f"#{timestamp}\n{index % 2}!\n")
    return (
        "$version frozen common activity $end\n"
        + timescale
        + "$scope module dut $end\n"
        + "$var wire 1 ! clk $end\n"
        + "$upscope $end\n"
        + "$enddefinitions $end\n"
        + "".join(values)
    ).encode("ascii")


def validation_bytes(source_bytes: bytes, duration: int = 20000) -> bytes:
    activity_cycles = duration // 10000
    return (
        "candidate=a3_p6_staged\n"
        f"vcd_sha256={hashlib.sha256(source_bytes).hexdigest()}\n"
        "window_start_tick_1ps=40000\n"
        f"window_end_tick_1ps={40000 + duration}\n"
        f"duration_tick_1ps={duration}\n"
        f"benchmark_measurement_cycles={activity_cycles - 1}\n"
        f"activity_window_ref_cycles={activity_cycles}\n"
        "window_contract=frozen_measurement_active_edges_plus_final_service\n"
        "scope=aer_clean_tb.candidate.dut\n"
    ).encode("ascii")


class TimestampScalerTest(unittest.TestCase):
    def run_tool(self, source: Path, output: Path, receipt: Path,
                 numerator: str = "1", denominator: str = "2",
                 *, validation: Path | None = None, check: bool = False,
                 memory_limit: int | None = None):
        def limit_memory() -> None:
            assert memory_limit is not None
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))

        if validation is None:
            validation = source.parent / "validation.txt"
            if not validation.exists():
                validation.write_bytes(validation_bytes(source.read_bytes()))
        return subprocess.run(
            ["python3", str(TOOL), "--input", str(source),
             "--validation-receipt", str(validation),
             "--output", str(output), "--receipt", str(receipt),
             "--numerator", numerator, "--denominator", denominator],
            check=check, capture_output=True, text=True,
            preexec_fn=limit_memory if memory_limit is not None else None,
            timeout=10,
        )

    def test_exact_half_and_deterministic_sha_receipt(self):
        source_bytes = vcd(timescale="$timescale\n  1 ps\n$end\n")
        expected = source_bytes.replace(b"#10000\n", b"#5000\n").replace(
            b"#20000\n", b"#10000\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "validated-10ns.vcd"
            source.write_bytes(source_bytes)
            receipts = []
            for suffix in ("a", "b"):
                output = root / f"scaled-{suffix}.vcd"
                receipt = root / f"scaled-{suffix}.json"
                self.run_tool(source, output, receipt, check=True)
                self.assertEqual(output.read_bytes(), expected)
                receipts.append(receipt.read_bytes())
                document = json.loads(receipt.read_bytes())
                self.assertEqual(document["schema"], "k2_w3_vcd_timestamp_scale_v1")
                self.assertEqual(document["transform"]["numerator"], 1)
                self.assertEqual(document["transform"]["denominator"], 2)
                self.assertEqual(document["transform"]["ratio"], "1/2")
                self.assertEqual(document["transform"]["input_clock_period_ps"], 10000)
                self.assertEqual(document["transform"]["output_clock_period_ps"], 5000)
                self.assertEqual(
                    document["input"]["sha256"], hashlib.sha256(source_bytes).hexdigest()
                )
                self.assertEqual(
                    document["output"]["sha256"], hashlib.sha256(expected).hexdigest()
                )
                self.assertEqual(document["timestamps"], {
                    "count": 3, "input_first": 0, "input_last": 20000,
                    "output_first": 0, "output_last": 10000,
                })
                self.assertEqual(document["source_validation"]["candidate"],
                                 "a3_p6_staged")
                self.assertTrue(receipt.read_bytes().endswith(b"\n"))
            self.assertEqual(receipts[0], receipts[1])

    def assert_rejected(self, source_bytes: bytes, message: str | None = None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, receipt = root / "in.vcd", root / "out.vcd", root / "out.json"
            source.write_bytes(source_bytes)
            result = self.run_tool(source, output, receipt)
            self.assertNotEqual(result.returncode, 0)
            if message is not None:
                self.assertIn(message, result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

    def test_nonintegral_and_malformed_timestamps_rejected(self):
        self.assert_rejected(vcd((0, 9999, 20000)), "not exactly divisible")
        for malformed in (b"#-2", b"#+2", b"#2.0", b"#2e3", b"#2 extra",
                          b" #2", "#２".encode("utf-8")):
            with self.subTest(timestamp=malformed):
                self.assert_rejected(vcd().replace(b"#10000", malformed), "timestamp")

    def test_header_and_timeline_fail_closed(self):
        cases = {
            "missing timescale": vcd().replace(b"$timescale 1ps $end\n", b""),
            "duplicate timescale": vcd().replace(
                b"$timescale 1ps $end\n", b"$timescale 1ps $end\n" * 2
            ),
            "wrong timescale": vcd(timescale="$timescale 10ps $end\n"),
            "missing enddefinitions": vcd().replace(b"$enddefinitions $end\n", b""),
            "nonmonotonic": vcd((0, 20000, 10000)),
            "nonzero first": vcd((2, 10000, 20000)),
            "zero duration": vcd((0, 0)),
            "unterminated directive": vcd().replace(
                b"$version frozen common activity $end\n", b"$comment never ends\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                self.assert_rejected(source)

    def test_hidden_or_malformed_timeline_material_rejected(self):
        for injected in (
            b"$comment data $end #6\n",
            b"0! #10000\n",
            b"$unknown #6 $end\n",
            b"$timezero 0 $end\n",
            b"$dumpvars\n#3\n0!\n$end\n",
        ):
            with self.subTest(injected=injected):
                source = vcd().replace(b"#10000\n", injected + b"#10000\n")
                self.assert_rejected(source)

    def test_validation_receipt_binds_sha_duration_and_10ns_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, validation = root / "in.vcd", root / "validation.txt"
            output, receipt = root / "out.vcd", root / "out.json"
            original = vcd()
            source.write_bytes(original)
            validation.write_bytes(validation_bytes(original))
            source.write_bytes(original.replace(b"0!\n", b"1!\n", 1))
            result = self.run_tool(source, output, receipt, validation=validation)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

            source.write_bytes(original)
            validation.write_bytes(validation_bytes(original).replace(
                b"activity_window_ref_cycles=2",
                b"activity_window_ref_cycles=3",
            ))
            result = self.run_tool(source, output, receipt, validation=validation)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

            validation.unlink()
            real_validation = root / "real-validation.txt"
            real_validation.write_bytes(validation_bytes(original))
            validation.symlink_to(real_validation)
            result = self.run_tool(source, output, receipt, validation=validation)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

    def test_comment_timestamp_is_not_scaled(self):
        source_bytes = vcd().replace(
            b"$scope module dut $end\n",
            b"$comment\n#3 is documentation, not time\n$end\n$scope module dut $end\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, receipt = root / "in.vcd", root / "out.vcd", root / "out.json"
            source.write_bytes(source_bytes)
            self.run_tool(source, output, receipt, check=True)
            self.assertIn(b"#3 is documentation, not time", output.read_bytes())
            self.assertIn(b"#5000\n", output.read_bytes())

    def test_dollar_identifier_code_is_value_data(self):
        source_bytes = vcd().replace(
            b"$var wire 1 ! clk $end\n",
            b"$var wire 1 $ counter $end\n",
        ).replace(b"0!\n", b"b0 $\n").replace(b"1!\n", b"b1 $\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, receipt = root / "in.vcd", root / "out.vcd", root / "out.json"
            source.write_bytes(source_bytes)
            self.run_tool(source, output, receipt, check=True)
            self.assertIn(b"b1 $\n", output.read_bytes())
            self.assertIn(b"#5000\n", output.read_bytes())

    def test_ratio_is_fixed_and_destinations_are_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, receipt = root / "in.vcd", root / "out.vcd", root / "out.json"
            source.write_bytes(vcd())
            for ratio in (("2", "4"), ("1", "3"), ("-1", "-2")):
                with self.subTest(ratio=ratio):
                    result = self.run_tool(source, output, receipt, *ratio)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())
                    self.assertFalse(receipt.exists())
            output.write_bytes(b"sentinel-output")
            result = self.run_tool(source, output, receipt)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b"sentinel-output")
            self.assertFalse(receipt.exists())
            output.unlink()
            receipt.write_bytes(b"sentinel-receipt")
            result = self.run_tool(source, output, receipt)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertEqual(receipt.read_bytes(), b"sentinel-receipt")

            result = self.run_tool(source, source, root / "in-place.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), vcd())

    def test_symlinks_and_path_aliases_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "in.vcd"
            source.write_bytes(vcd())
            source_link = root / "input-link.vcd"
            source_link.symlink_to(source)
            result = self.run_tool(source_link, root / "a.vcd", root / "a.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "a.vcd").exists())
            self.assertFalse((root / "a.json").exists())

            dangling = root / "out.vcd"
            dangling.symlink_to(root / "missing")
            result = self.run_tool(source, dangling, root / "b.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(dangling.is_symlink())
            self.assertFalse((root / "b.json").exists())

            parent = root / "real-parent"
            parent.mkdir()
            parent_link = root / "parent-link"
            parent_link.symlink_to(parent, target_is_directory=True)
            result = self.run_tool(source, parent_link / "out.vcd", root / "c.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((parent / "out.vcd").exists())

            result = self.run_tool(source, root / "same", root / "same")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "same").exists())

    def test_fifo_input_rejects_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fifo = root / "input.fifo"
            os.mkfifo(fifo)
            validation = root / "validation.txt"
            validation.write_bytes(validation_bytes(vcd()))
            result = self.run_tool(
                fifo, root / "out.vcd", root / "out.json", validation=validation
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "out.vcd").exists())
            self.assertFalse((root / "out.json").exists())

    def test_streaming_under_bounded_address_space(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, receipt = root / "large.vcd", root / "out.vcd", root / "out.json"
            with source.open("wb") as stream:
                stream.write(
                    b"$timescale 1ps $end\n$comment\n" +
                    (b"streaming-padding-0123456789abcdef\n" * 350000) +
                    b"$end\n$enddefinitions $end\n#0\n#20000\n"
                )
            self.assertGreater(source.stat().st_size, 10 * 1024 * 1024)
            result = self.run_tool(source, output, receipt,
                                   memory_limit=48 * 1024 * 1024)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(receipt.read_text())["timestamps"]["output_last"], 10000)


if __name__ == "__main__":
    unittest.main()
