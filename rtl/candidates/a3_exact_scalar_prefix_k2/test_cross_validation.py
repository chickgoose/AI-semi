from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


HERE = pathlib.Path(__file__).resolve().parent
CROSS = HERE / "cross_validation"
REPO = HERE.parents[2]
sys.path.insert(0, str(CROSS))
import a5_trace_exporter as exporter  # noqa: E402


A5_GENERATOR = "tests/a5_k2_common_evaluator/generate_vectors.py"
A5_ORACLE = "tests/a5_k2_common_evaluator/k2_oracle.py"
LEGACY_EXPORTER = (
    "rtl/candidates/a3_exact_scalar_prefix_k2/"
    "cross_validation/a5_trace_exporter.py"
)
EXPECTED_BUNDLE_SHA256 = "efa202c4ebd91caff2573d9ccd7956b1a1e5584b999fc001fccb02e2a8388f75"


def git_blob(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=REPO,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


def load_a5_bundle(work: pathlib.Path) -> dict:
    package = work / "a5"
    package.mkdir()
    (package / "generate_vectors.py").write_bytes(
        git_blob(exporter.A5_COMMIT, A5_GENERATOR)
    )
    (package / "k2_oracle.py").write_bytes(
        git_blob(exporter.A5_COMMIT, A5_ORACLE)
    )
    output = work / "vectors.json"
    result = subprocess.run(
        [sys.executable, "-B", str(package / "generate_vectors.py"),
         "--output", str(output)], cwd=work, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stdout)
    bundle = json.loads(output.read_text(encoding="utf-8"))
    if bundle.get("bundle_sha256") != EXPECTED_BUNDLE_SHA256:
        raise RuntimeError("pinned A5 vector bundle SHA mismatch")
    return bundle


def load_legacy_exporter() -> types.ModuleType:
    source = git_blob(exporter.LEGACY_EXPORTER_COMMIT, LEGACY_EXPORTER)
    module = types.ModuleType("a3_legacy_29a5003_exporter")
    module.__file__ = str(CROSS / "a5_trace_exporter.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def required_tool(name: str, fallback: pathlib.Path) -> pathlib.Path:
    found = shutil.which(name)
    path = pathlib.Path(found) if found else fallback
    if not (path.is_file() and path.stat().st_mode & 0o111):
        raise RuntimeError(f"required tool missing: {name}")
    return path


class CrossValidationTests(unittest.TestCase):
    def test_owner_and_oracle_provenance_is_full_sha_bound(self) -> None:
        provenance = exporter.verify_provenance()
        self.assertEqual(exporter.OWNER_COMMIT,
                         "632e68d247ec36a35b62dbd5c100b0a23d47cf7b")
        self.assertEqual(provenance["owner_oracle_sha256"],
                         exporter.EXPECTED_OWNER_ORACLE_SHA256)
        self.assertEqual(provenance["a5_oracle_sha256"],
                         exporter.EXPECTED_A5_ORACLE_SHA256)

    def test_actual_a5_first_divergence_rejects_latency_normalization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-k2-a5-vector-") as temporary:
            bundle = load_a5_bundle(pathlib.Path(temporary))
        vector = next(run for run in bundle["runs"]
                      if run["name"] == "persistent_weight_120")
        legacy = load_legacy_exporter().export_run(vector)
        faithful = exporter.export_run(vector)
        differences = [
            (old["cycle"], old["accepts"], new["accepts"])
            for old, new in zip(legacy["cycles"], faithful["cycles"])
            if old["accepts"] != new["accepts"]
        ]
        self.assertTrue(differences)
        cycle, old_accepts, faithful_accepts = differences[0]
        self.assertEqual(cycle, 2)
        self.assertEqual([item["source"] for item in old_accepts], [4, 11])
        self.assertEqual(faithful_accepts, [])
        self.assertEqual(
            [item["source"] for item in faithful["cycles"][3]["accepts"]],
            [4, 11],
        )

    def test_owner_latency_and_charged_link_rtl(self) -> None:
        iverilog = required_tool(
            "iverilog", pathlib.Path("/tmp/a7-toolchain/usr/bin/iverilog")
        )
        vvp = required_tool("vvp", pathlib.Path("/tmp/a7-toolchain/usr/bin/vvp"))
        owner = HERE / "rtl/a3_exact_scalar_prefix_k2.sv"
        cases = (
            ("a3_k2_a5_owner_latency_tb", CROSS / "a5_owner_latency_tb.sv",
             (owner,), "A3_K2_A5_OWNER_REGISTERED_LATENCY_PASS"),
            ("a3_k2_a5_link_tb", CROSS / "a5_link_tb.sv",
             (exporter.ADAPTER,), "A3_K2_A5_CHARGED_LINK_PASS"),
        )
        with tempfile.TemporaryDirectory(prefix="a3-k2-a5-rtl-") as temporary:
            for top, tb, rtl_files, marker in cases:
                image = pathlib.Path(temporary) / f"{top}.vvp"
                compile_result = subprocess.run(
                    [str(iverilog), "-g2012", "-Wall", "-s", top,
                     "-o", str(image), *(str(path) for path in rtl_files), str(tb)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(compile_result.returncode, 0, compile_result.stdout)
                self.assertNotIn("warning", compile_result.stdout.lower())
                run_result = subprocess.run(
                    [str(vvp), str(image)], text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, check=False,
                )
                self.assertEqual(run_result.returncode, 0, run_result.stdout)
                self.assertEqual(run_result.stdout.count(marker), 1, run_result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
