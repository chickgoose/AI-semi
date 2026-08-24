from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge import official_functional_run as module
from benchmarks.redred_cluster2_cav_bridge.contract import canonical_json_bytes


ROOT = Path(module.__file__).parents[2]
GOLDEN = ROOT / (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_result.json"
)


def _artifact(path, sha256, size=1):
    return {"path": path, "sha256": sha256, "size_bytes": size}


class SyntheticOfficialFunctionalRunTests(unittest.TestCase):
    digest = "a" * 64

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.dataset = self.directory / "private-host-dataset"
        self.dataset.mkdir()
        self.cyclemask = self.directory / "private-host-mask.txt"
        self.output = self.directory / "result.json"
        self.source = object()
        self.outcomes = object()
        statistics = SimpleNamespace(
            event_count=8503,
            pose_count=11883,
            exact_join_count=8503,
            decision_count=8503,
            mode_counts=(("causal_cav", 8420), ("zoh_fallback", 0),
                         ("sensor_fixed_bypass", 83)),
            frame_counts=(("WORLD", 8420), ("SENSOR_FIXED", 83)),
            latency_histogram=((1, 6393), (2, 2077), (3, 33)),
            transport_time_semantics=module.TRANSPORT_TIME_SEMANTICS,
            grid_width=512,
            grid_height=256,
            grid_quantized_count=8420,
            grid_unique_count=821,
            grid_x_min=238,
            grid_x_max=298,
            grid_y_min=93,
            grid_y_max=165,
            grid_index_min=47876,
            grid_index_max=84754,
            coordinate_convention=module.COORDINATE_CONVENTION,
            join_identity_sha256=module.EXPECTED_RESULT_DIGESTS[
                "join_identity_sha256"
            ],
            geometry_sha256=module.EXPECTED_RESULT_DIGESTS["geometry_sha256"],
            retire_sidecar_sha256=module.EXPECTED_RESULT_DIGESTS[
                "retire_sidecar_sha256"
            ],
            grid_sha256=module.EXPECTED_RESULT_DIGESTS["world_grid_sha256"],
        )
        geometry = object()
        views = tuple(
            SimpleNamespace(
                view_name=name,
                geometry_sha256=module.EXPECTED_RESULT_DIGESTS["geometry_sha256"],
                            geometry=geometry)
            for name in module.VIEW_ORDER
        )
        self.result = SimpleNamespace(statistics=statistics, views=views)

    def _sources(self):
        pins = module.OFFICIAL_SOURCE_PINS
        return [
            _artifact("official_uzh/shapes_rotation/events.txt",
                      pins.events_sha256, pins.events_size_bytes),
            _artifact("official_uzh/shapes_rotation/groundtruth.txt",
                      pins.groundtruth_sha256,
                      module.EXPECTED_GROUNDTRUTH_SIZE),
            _artifact("official_uzh/shapes_rotation/calib.txt",
                      pins.calibration_sha256,
                      module.EXPECTED_CALIBRATION_SIZE),
        ]

    def _cyclemask(self):
        return {
            "path": module.CYCLEMASK_RELATIVE_PATH,
            "observed_line_endings": "LF",
            "observed_raw_sha256": module.CYCLEMASK_LF_SHA256,
            "observed_size_bytes": 1,
            "canonical_semantic_lf_sha256": module.CYCLEMASK_LF_SHA256,
            "accepted_raw_encodings": [
                {"line_endings": "LF", "sha256": module.CYCLEMASK_LF_SHA256},
                {"line_endings": "CRLF",
                 "sha256": module.CYCLEMASK_CRLF_SHA256},
            ],
        }

    def _repository(self):
        return module._repository_authorities(ROOT)

    def _patched(self, order=None):
        if order is None:
            order = []

        def source(dataset, cyclemask):
            order.append("build_source")
            self.assertEqual(dataset, self.dataset)
            self.assertEqual(cyclemask, self.cyclemask)
            return self.source

        def outcomes(root):
            order.append("load_outcomes")
            return self.outcomes

        def run(source_value, outcome_value):
            order.append("run_assay")
            self.assertIs(source_value, self.source)
            self.assertIs(outcome_value, self.outcomes)
            return self.result

        def validate(result, source_value, outcome_value):
            order.append("validate_result")
            self.assertIs(result, self.result)
            self.assertIs(source_value, self.source)
            self.assertIs(outcome_value, self.outcomes)
            return result

        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            module, "build_official_uzh_functional_source", side_effect=source
        ))
        stack.enter_context(mock.patch.object(
            module, "load_abaa094_native_outcomes", side_effect=outcomes
        ))
        stack.enter_context(mock.patch.object(
            module, "run_functional_assay", side_effect=run
        ))
        stack.enter_context(mock.patch.object(
            module, "validate_functional_assay_result", side_effect=validate
        ))
        stack.enter_context(mock.patch.object(
            module, "_source_authorities", return_value=self._sources()
        ))
        stack.enter_context(mock.patch.object(
            module, "_cyclemask_authority", return_value=self._cyclemask()
        ))
        stack.enter_context(mock.patch.object(
            module, "_repository_authorities", return_value=self._repository()
        ))
        return stack

    def test_mandatory_pipeline_order_and_sealed_summary(self):
        order = []
        with self._patched(order):
            summary = module.build_official_functional_summary(
                self.dataset, self.cyclemask, ROOT
            )
        self.assertEqual(order, [
            "build_source", "load_outcomes", "run_assay", "validate_result"
        ])
        self.assertIs(module.validate_official_functional_summary(summary), summary)
        self.assertEqual(summary["population"]["events"], 8503)
        self.assertEqual(summary["population"]["poses"], 11883)
        self.assertEqual(summary["world_grid"]["unique_cell_count"], 821)
        self.assertTrue(summary["three_view_equality"][
            "all_geometry_digests_equal"
        ])

    def test_atomic_canonical_output_contains_no_cli_host_paths(self):
        with self._patched():
            result = module.write_official_functional_summary(
                self.dataset, self.cyclemask, self.output, ROOT
            )
        raw = self.output.read_bytes()
        self.assertEqual(raw, canonical_json_bytes(result))
        self.assertNotIn(str(self.directory).encode("ascii"), raw)
        self.assertNotIn(b"private-host", raw)
        decoded = json.loads(raw.decode("ascii"))
        module.validate_official_functional_summary(decoded)

    def test_cli_accepts_three_paths_without_serializing_them(self):
        with self._patched():
            self.assertEqual(module.main([
                "--dataset", str(self.dataset),
                "--cyclemask", str(self.cyclemask),
                "--output", str(self.output),
            ]), 0)
        self.assertNotIn(str(self.directory), self.output.read_text("ascii"))

    def test_mutated_count_digest_claim_and_seal_fail_closed(self):
        with self._patched():
            baseline = module.build_official_functional_summary(
                self.dataset, self.cyclemask, ROOT
            )
        mutations = []
        changed = copy.deepcopy(baseline)
        changed["population"]["events"] = 8502
        mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["digests"]["world_grid_sha256"] = "e" * 64
        mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["claim_scope"]["wire_complete_cav_rtl"] = "PASS"
        mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["seal"]["sha256"] = "0" * 64
        mutations.append(changed)
        for index, value in enumerate(mutations):
            if index != len(mutations) - 1:
                body = dict(value)
                body.pop("seal")
                value["seal"]["sha256"] = module._canonical_sha256(body)
            with self.subTest(value=value):
                with self.assertRaises(module.OfficialFunctionalRunError):
                    module.validate_official_functional_summary(value)

    def test_every_json_value_requires_exact_builtin_schema_type(self):
        class IntSubclass(int):
            pass

        class StrSubclass(str):
            pass

        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        with self._patched():
            baseline = module.build_official_functional_summary(
                self.dataset, self.cyclemask, ROOT
            )
        mutations = []
        for replacement in (8503.0, IntSubclass(8503)):
            changed = copy.deepcopy(baseline)
            changed["population"]["events"] = replacement
            mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["population"]["zoh_fallback"] = False
        mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["status"] = StrSubclass(changed["status"])
        mutations.append(changed)
        changed = DictSubclass(copy.deepcopy(baseline))
        mutations.append(changed)
        changed = copy.deepcopy(baseline)
        changed["world_grid"]["x_range_inclusive"] = ListSubclass([238, 298])
        mutations.append(changed)
        for value in mutations:
            with self.subTest(replacement=type(value).__name__):
                with self.assertRaises(module.OfficialFunctionalRunError):
                    module.validate_official_functional_summary(value)

    def test_public_validator_always_rehashes_default_current_repository(self):
        with self._patched():
            baseline = module.build_official_functional_summary(
                self.dataset, self.cyclemask, ROOT
            )
        original = module._repository_authorities
        with mock.patch.object(module, "_repository_authorities",
                               wraps=original) as observed:
            module.validate_official_functional_summary(baseline)
        observed.assert_called_once_with(Path(module.__file__).parents[2])

        changed = copy.deepcopy(baseline)
        changed["input_authority"]["core_code"][0]["sha256"] = "e" * 64
        body = dict(changed)
        body.pop("seal")
        changed["seal"]["sha256"] = module._canonical_sha256(body)
        with self.assertRaisesRegex(
            module.OfficialFunctionalRunError, "actual files"
        ):
            module.validate_official_functional_summary(changed)

    def test_core_code_is_exact_loaded_in_repo_python_module_closure(self):
        import subprocess
        import sys

        program = r'''
import json
import sys
from pathlib import Path
root = Path('.').resolve()
import benchmarks.redred_cluster2_cav_bridge.official_functional_run as runner
loaded = []
for value in sys.modules.values():
    filename = getattr(value, '__file__', None)
    if not filename:
        continue
    try:
        relative = Path(filename).resolve().relative_to(root)
    except (OSError, ValueError):
        continue
    if relative.suffix == '.py':
        loaded.append(relative.as_posix())
print(json.dumps({'loaded': sorted(set(loaded)),
                  'authority': sorted(runner.CORE_CODE_PATHS)}))
'''
        completed = subprocess.run(
            [sys.executable, "-c", program], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        closure = json.loads(completed.stdout)
        self.assertEqual(len(module.CORE_CODE_PATHS), 30)
        self.assertEqual(closure["loaded"], closure["authority"])

    def test_absolute_and_noncanonical_authority_paths_fail_closed(self):
        with self._patched():
            baseline = module.build_official_functional_summary(
                self.dataset, self.cyclemask, ROOT
            )
        for path in ("/tmp/events.txt", "a//events.txt", "a/../events.txt",
                     "a\\events.txt", "a\x00events.txt"):
            changed = copy.deepcopy(baseline)
            changed["input_authority"]["official_sources"][0]["path"] = path
            body = dict(changed)
            body.pop("seal")
            changed["seal"]["sha256"] = module._canonical_sha256(body)
            with self.subTest(path=path):
                with self.assertRaises(module.OfficialFunctionalRunError):
                    module.validate_official_functional_summary(changed)

    def test_result_validator_must_return_exact_result(self):
        with self._patched() as stack:
            stack.enter_context(mock.patch.object(
                module, "validate_functional_assay_result", return_value=object()
            ))
            with self.assertRaisesRegex(
                module.OfficialFunctionalRunError, "retain the exact result"
            ):
                module.build_official_functional_summary(
                    self.dataset, self.cyclemask, ROOT
                )

    def test_cyclemask_lf_and_crlf_share_semantic_digest(self):
        lf = b"1 0001\n2 0002\n"
        crlf = lf.replace(b"\n", b"\r\n")
        with mock.patch.object(
            module, "CYCLEMASK_LF_SHA256", hashlib.sha256(lf).hexdigest()
        ), mock.patch.object(
            module, "CYCLEMASK_CRLF_SHA256", hashlib.sha256(crlf).hexdigest()
        ):
            rows = []
            for name, payload in (("lf", lf), ("crlf", crlf)):
                path = self.directory / name
                path.write_bytes(payload)
                rows.append(module._cyclemask_authority(path))
        self.assertEqual(rows[0]["observed_line_endings"], "LF")
        self.assertEqual(rows[1]["observed_line_endings"], "CRLF")
        self.assertEqual(rows[0]["canonical_semantic_lf_sha256"],
                         rows[1]["canonical_semantic_lf_sha256"])

    def test_cyclemask_mixed_endings_fail_closed(self):
        payload = b"1 0001\r\n2 0002\n"
        path = self.directory / "mixed"
        path.write_bytes(payload)
        with self.assertRaises(module.OfficialFunctionalRunError):
            module._cyclemask_authority(path)


@unittest.skipUnless(GOLDEN.is_file(), "official committed result not generated")
class CommittedGoldenContractTests(unittest.TestCase):
    def test_committed_result_is_canonical_and_sealed(self):
        raw = GOLDEN.read_bytes()
        decoded = json.loads(raw.decode("ascii"))
        self.assertEqual(raw, canonical_json_bytes(decoded))
        module.validate_official_functional_summary(decoded, ROOT)
        self.assertNotIn(b"/tmp/", raw)


@unittest.skipUnless(
    os.environ.get("REDRED_RUN_CLUSTER2_FUNCTIONAL_ASSAY_OFFICIAL") == "1",
    "set REDRED_RUN_CLUSTER2_FUNCTIONAL_ASSAY_OFFICIAL=1 for exact replay",
)
class EnvironmentGatedOfficialGoldenReplay(unittest.TestCase):
    def test_exact_official_replay_matches_committed_golden(self):
        dataset = os.environ.get("REDRED_UZH_SHAPES_ROTATION_ROOT")
        cyclemask = os.environ.get("REDRED_CLUSTER2_CYCLEMASK_PATH")
        if not dataset or not cyclemask:
            self.fail("official dataset and cyclemask environment paths are required")
        actual = module.build_official_functional_summary(
            Path(dataset), Path(cyclemask), ROOT
        )
        expected = json.loads(GOLDEN.read_text("ascii"))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
