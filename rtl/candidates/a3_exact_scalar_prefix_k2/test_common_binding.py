from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
DIRECTED = HERE / "tb/common_directed.f"
BINDING = HERE / "tb/common_binding_directed.f"


def required_tool(name: str, fallback: pathlib.Path) -> pathlib.Path:
    found = shutil.which(name)
    path = pathlib.Path(found) if found else fallback
    if not (path.is_file() and path.stat().st_mode & 0o111):
        raise RuntimeError(f"required tool missing: {name}")
    return path


class CommonBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.iverilog = required_tool(
            "iverilog", pathlib.Path("/tmp/a7-toolchain/usr/bin/iverilog")
        )
        cls.vvp = required_tool(
            "vvp", pathlib.Path("/tmp/a7-toolchain/usr/bin/vvp")
        )
        cls.verilator = required_tool(
            "verilator", pathlib.Path("/tmp/a7-toolchain/usr/bin/verilator")
        )

    def test_count1_lane1_ready_independence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-k2-adapter-directed-") as tmp:
            image = pathlib.Path(tmp) / "adapter.vvp"
            compile_result = subprocess.run(
                [str(self.iverilog), "-g2012", "-Wall", "-s",
                 "a3_k2_ordered_adapter_tb", "-f", str(DIRECTED),
                 "-o", str(image)],
                cwd=REPO, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout)
            run_result = subprocess.run(
                [str(self.vvp), str(image)], cwd=REPO, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stdout)
            self.assertEqual(
                run_result.stdout.count("A3_K2_ORDERED_ADAPTER_PASS"), 1,
                run_result.stdout,
            )

    def _build_and_run_binding(
        self, build: pathlib.Path, defines: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(self.verilator), "--binary", "--timing", "-j", "2",
            "--top-module", "a3_k2_common_binding_tb", "-Wno-fatal",
            "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-BLKSEQ",
            "--Mdir", str(build),
        ]
        command.extend(f"-D{define}" for define in defines)
        command.extend(["-f", str(BINDING)])
        compile_result = subprocess.run(
            command, cwd=REPO, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout)
        return subprocess.run(
            [str(build / "Va3_k2_common_binding_tb")], cwd=REPO,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False,
        )

    def test_candidate_modport_and_corner_sequences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-k2-binding-directed-") as tmp:
            result = self._build_and_run_binding(pathlib.Path(tmp) / "obj")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.count("A3_K2_COMMON_BINDING_PASS"), 1,
                         result.stdout)

    def test_uniform_ready_and_fifo_depth_guards_fail_closed(self) -> None:
        cases = (
            ("nonuniform", ("A3_K2_TEST_NONUNIFORM_READY",),
             "supports uniform retire_ready only"),
            ("fifo_depth", ("A3_K2_TEST_FIFO_DEPTH_NONZERO",),
             "requires compatibility FIFO_DEPTH=0"),
        )
        with tempfile.TemporaryDirectory(prefix="a3-k2-binding-guards-") as tmp:
            root = pathlib.Path(tmp)
            for name, defines, marker in cases:
                result = self._build_and_run_binding(root / name, defines)
                self.assertNotEqual(result.returncode, 0,
                                    f"{name} guard escaped\n{result.stdout}")
                self.assertIn(marker, result.stdout)

    def test_lane_swap_mutations_are_rejected(self) -> None:
        mutations = (
            "A3_K2_MUT_EVENT_LANE_SWAP",
            "A3_K2_MUT_SOURCE_LANE_SWAP",
        )
        with tempfile.TemporaryDirectory(prefix="a3-k2-binding-mutations-") as tmp:
            root = pathlib.Path(tmp)
            for mutation in mutations:
                result = self._build_and_run_binding(root / mutation, (mutation,))
                self.assertNotEqual(
                    result.returncode, 0,
                    f"lane-swap mutation escaped: {mutation}\n{result.stdout}",
                )
                self.assertNotIn("A3_K2_COMMON_BINDING_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
