from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import inspect
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge import functional_assay as module
from benchmarks.redred_cluster2_cav_bridge.cav_adapter import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from benchmarks.redred_cluster2_cav_bridge.contract import (
    canonical_event_content_sha256,
    canonical_json_bytes,
)
from benchmarks.redred_cluster2_cav_bridge.functional_source import (
    FunctionalSourceBundle,
    NativeEventIdentity,
    build_official_uzh_functional_source,
)
from benchmarks.redred_cluster2_cav_bridge.native_outcome_bundle import (
    NativeOutcome,
    load_abaa094_native_outcomes,
)
from benchmarks.redred_cluster2_cav_bridge.transport_time import (
    TRANSPORT_TIME_SEMANTICS,
)
from benchmarks.redred_mc_wtb_pose_recovery import RecoveryMode
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


def _pose_digest(pose_id, timestamp_ns, quaternion):
    return hashlib.sha256(canonical_json_bytes({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion),
    })).hexdigest()


def _pose(pose_id, timestamp_ns):
    quaternion = (0.0, 0.0, 0.0, 1.0)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        _pose_digest(pose_id, timestamp_ns, quaternion),
        True,
        True,
    )


def _event(event_id, timestamp_ns, sensor_ray):
    digest = canonical_event_content_sha256(
        event_id,
        timestamp_ns,
        1,
        True,
        sensor_ray,
        1,
        True,
    )
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        1,
        True,
        sensor_ray,
        1,
        digest,
        True,
    )


def _synthetic_inputs():
    # IDs deliberately differ from CAV timestamp order so the assay cannot
    # accidentally zip source events to event-ID-ordered native outcomes.
    events = (
        _event(1, 1_000, (1.0, 0.0, 0.0)),
        _event(0, 2_000, (0.0, 1.0, 0.0)),
        _event(2, 2_000_000, (0.0, 0.0, 1.0)),
    )
    identities = (
        NativeEventIdentity(1, 0, 10, 110, 85),
        NativeEventIdentity(0, 1, 20, 111, 85),
        NativeEventIdentity(2, 2, 30, 112, 85),
    )
    source = FunctionalSourceBundle(
        registry=NeutralRegistryWindow(
            "synthetic-functional-assay", 0, 1_000, 3_000_000
        ),
        events=events,
        poses=(_pose(0, 500), _pose(1, 900)),
        native_identities=identities,
        required_pose_start_id=0,
        required_pose_end_id=1,
        required_pose_pre_roll_ns=1,
        causal_cav_eligible_count=1,
        fresh_zoh_fallback_count=1,
        stale_pose_count=1,
    )
    outcomes = (
        NativeOutcome(0, 1, 20, 22, 2),
        NativeOutcome(1, 0, 10, 11, 1),
        NativeOutcome(2, 2, 30, 33, 3),
    )
    return source, outcomes


@contextmanager
def _synthetic_authority():
    with mock.patch.multiple(
        module,
        EXPECTED_EVENT_COUNT=3,
        EXPECTED_POSE_COUNT=2,
        EXPECTED_CAUSAL_CAV_COUNT=1,
        EXPECTED_ZOH_COUNT=1,
        EXPECTED_BYPASS_COUNT=1,
    ):
        yield


def _coherent_geometry_result(result, index, ray):
    """Build an internally coherent result around one alternate geometry ray."""

    geometry = list(result.geometry)
    grid = module.quantize_world_ray(ray, module.GRID_WIDTH, module.GRID_HEIGHT)
    geometry[index] = replace(geometry[index], ray_xyz=ray, world_grid=grid)
    geometry = tuple(geometry)
    derived = module._derive_row_statistics(geometry, result.retire_sidecar)
    digest = derived["geometry_sha256"]
    statistics = replace(
        result.statistics,
        mode_counts=derived["mode_counts"],
        frame_counts=derived["frame_counts"],
        latency_histogram=derived["latency_histogram"],
        grid_quantized_count=derived["grid_quantized_count"],
        grid_unique_count=derived["grid_unique_count"],
        grid_x_min=derived["grid_x_min"],
        grid_x_max=derived["grid_x_max"],
        grid_y_min=derived["grid_y_min"],
        grid_y_max=derived["grid_y_max"],
        grid_index_min=derived["grid_index_min"],
        grid_index_max=derived["grid_index_max"],
        join_identity_sha256=derived["join_identity_sha256"],
        geometry_sha256=digest,
        retire_sidecar_sha256=derived["retire_sidecar_sha256"],
        grid_sha256=derived["grid_sha256"],
        view_geometry_sha256=tuple(
            (name, digest) for name in module.VIEW_ORDER
        ),
    )
    views = tuple(replace(
        view, geometry=geometry, geometry_sha256=digest
    ) for view in result.views)
    return module.FunctionalAssayResult(views, statistics)


class FunctionalAssaySyntheticTests(unittest.TestCase):
    def test_exact_join_precedes_geometry_and_uses_event_id_not_position(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
        self.assertEqual([row.event_id for row in result.geometry], [1, 0, 2])
        self.assertEqual(
            [row.event_id for row in result.retire_sidecar], [1, 0, 2]
        )

        changed = list(outcomes)
        changed[0] = replace(changed[0], source=3)
        with _synthetic_authority(), mock.patch.object(
            module, "_run_geometry"
        ) as geometry:
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "event_id/source/native occurrence exact join",
            ):
                module.run_functional_assay(source, tuple(changed))
        geometry.assert_not_called()

        changed = list(outcomes)
        changed[0] = replace(changed[0], occurrence_cycle=21, retire_cycle=23)
        with _synthetic_authority(), mock.patch.object(
            module, "_run_geometry"
        ) as geometry:
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "event_id/source/native occurrence exact join",
            ):
                module.run_functional_assay(source, tuple(changed))
        geometry.assert_not_called()

    def test_three_views_share_geometry_and_bypass_never_enters_world_grid(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)

        self.assertEqual(
            tuple(view.view_name for view in result.views), module.VIEW_ORDER
        )
        self.assertTrue(all(
            view.geometry is result.geometry for view in result.views
        ))
        self.assertEqual(
            len(set(view.geometry_sha256 for view in result.views)), 1
        )
        self.assertEqual(result.views[0].transport_sidecar, ())
        self.assertEqual(result.views[1].transport_sidecar, ())
        self.assertIs(result.views[2].transport_sidecar, result.retire_sidecar)
        self.assertIs(result.latency_sidecar, result.retire_sidecar)
        self.assertIs(result.views[2].latency_sidecar, result.retire_sidecar)
        self.assertIsNone(result.views[0].sidecar_semantics)
        self.assertIsNone(result.views[1].sidecar_semantics)
        self.assertEqual(
            result.views[2].sidecar_semantics, module.LATENCY_SIDECAR_ONLY
        )
        self.assertEqual(
            result.statistics.mode_counts,
            (
                (RecoveryMode.CAV.value, 1),
                (RecoveryMode.ZOH.value, 1),
                (RecoveryMode.BYPASS.value, 1),
            ),
        )
        self.assertEqual(
            result.statistics.frame_counts,
            ((module.WORLD_FRAME, 2), (module.SENSOR_FIXED_FRAME, 1)),
        )
        bypass = result.geometry[2]
        self.assertIs(bypass.recovery_mode, RecoveryMode.BYPASS)
        self.assertEqual(bypass.coordinate_frame, module.SENSOR_FIXED_FRAME)
        self.assertIsNone(bypass.world_grid)
        self.assertEqual(result.statistics.grid_quantized_count, 2)
        self.assertEqual(result.statistics.grid_unique_count, 2)
        self.assertTrue(
            0 <= result.statistics.grid_index_min
            <= result.statistics.grid_index_max < 512 * 256
        )

    def test_transport_and_join_mutations_leave_geometry_digest_unchanged(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            baseline = module.run_functional_assay(source, outcomes)

        retire_changed = list(outcomes)
        retire_changed[0] = replace(
            retire_changed[0], retire_cycle=23, latency=3
        )
        with _synthetic_authority():
            retired = module.run_functional_assay(
                source, tuple(retire_changed)
            )
        self.assertEqual(
            retired.statistics.geometry_sha256,
            baseline.statistics.geometry_sha256,
        )
        self.assertNotEqual(
            retired.statistics.retire_sidecar_sha256,
            baseline.statistics.retire_sidecar_sha256,
        )

        identities = list(source.native_identities)
        identities[1] = NativeEventIdentity(0, 3, 25, 113, 85)
        identity_source = replace(source, native_identities=tuple(identities))
        identity_outcomes = list(outcomes)
        identity_outcomes[0] = NativeOutcome(0, 3, 25, 27, 2)
        with _synthetic_authority():
            rejoined = module.run_functional_assay(
                identity_source, tuple(identity_outcomes)
            )
        self.assertEqual(
            rejoined.statistics.geometry_sha256,
            baseline.statistics.geometry_sha256,
        )
        self.assertNotEqual(
            rejoined.statistics.join_identity_sha256,
            baseline.statistics.join_identity_sha256,
        )
        self.assertNotEqual(
            rejoined.statistics.retire_sidecar_sha256,
            baseline.statistics.retire_sidecar_sha256,
        )

    def test_latency_histogram_and_sidecar_use_only_two_ns_delta_time(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
        self.assertEqual(
            result.statistics.latency_histogram, ((1, 1), (2, 1), (3, 1))
        )
        by_id = dict((row.event_id, row) for row in result.retire_sidecar)
        original_by_id = dict((row.event_id, row) for row in source.events)
        self.assertEqual(
            module.SIDECAR_ORDER, ("retire_cycle", "event_id")
        )
        self.assertEqual(
            tuple((row.retire_cycle, row.event_id) for row in result.retire_sidecar),
            tuple(sorted(
                (row.retire_cycle, row.event_id)
                for row in result.retire_sidecar
            )),
        )
        for event_id, row in by_id.items():
            self.assertEqual(row.latency_ns, row.latency_cycles * 2)
            self.assertEqual(
                row.latency_injected_timestamp_ns,
                original_by_id[event_id].timestamp_ns + row.latency_ns,
            )
            self.assertEqual(
                row.dual_time.semantics_label, TRANSPORT_TIME_SEMANTICS
            )
            self.assertFalse(any(hasattr(row, name) for name in (
                "retire_ordinal",
                "retire_native_lane",
                "retire_row",
                "retire_col",
            )))
        self.assertTrue(all(
            not hasattr(row, "native_occurrence_cycle")
            and not hasattr(row, "retire_cycle")
            for row in result.geometry
        ))
        self.assertEqual(
            tuple(inspect.signature(module._run_geometry).parameters),
            ("source",),
        )

    def test_result_records_fail_closed(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
        with self.assertRaisesRegex(
            module.FunctionalAssayError, "WORLD geometry must have"
        ):
            replace(result.geometry[0], world_grid=None)
        with self.assertRaisesRegex(
            module.FunctionalAssayError, "only AER-RET-CAV"
        ):
            replace(result.views[0], transport_sidecar=result.retire_sidecar)

    def test_direct_world_grid_must_be_recomputed_from_the_ray(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
        world = result.geometry[0]
        unrelated = module.quantize_world_ray(
            (0.0, 1.0, 0.0), module.GRID_WIDTH, module.GRID_HEIGHT
        )
        if unrelated == world.world_grid:
            unrelated = module.quantize_world_ray(
                (-1.0, 0.0, 0.0), module.GRID_WIDTH, module.GRID_HEIGHT
            )
        self.assertNotEqual(unrelated, world.world_grid)
        with self.assertRaisesRegex(
            module.FunctionalAssayError,
            "WORLD grid differs from quantized geometry ray",
        ):
            replace(world, world_grid=unrelated)

    def test_direct_result_recomputes_all_digests_and_row_statistics(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
        fake = "0" * 64
        if fake == result.statistics.geometry_sha256:
            fake = "1" * 64

        with _synthetic_authority():
            fake_statistics = replace(
                result.statistics,
                geometry_sha256=fake,
                view_geometry_sha256=tuple(
                    (name, fake) for name in module.VIEW_ORDER
                ),
            )
            fake_views = tuple(
                replace(view, geometry_sha256=fake) for view in result.views
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "geometry_sha256 differs from actual rows",
            ):
                module.FunctionalAssayResult(fake_views, fake_statistics)

            for field in (
                "join_identity_sha256",
                "retire_sidecar_sha256",
                "grid_sha256",
            ):
                with self.subTest(field=field):
                    fake_statistics = replace(
                        result.statistics, **{field: fake}
                    )
                    with self.assertRaisesRegex(
                        module.FunctionalAssayError,
                        "%s differs from actual rows" % field,
                    ):
                        module.FunctionalAssayResult(
                            result.views, fake_statistics
                        )

            fake_statistics = replace(
                result.statistics, latency_histogram=((1, 3),)
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "latency_histogram differs from actual rows",
            ):
                module.FunctionalAssayResult(result.views, fake_statistics)

            fake_statistics = replace(result.statistics, grid_unique_count=1)
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "grid_unique_count differs from actual rows",
            ):
                module.FunctionalAssayResult(result.views, fake_statistics)

            for field, value in (
                ("event_count", 2),
                ("pose_count", 1),
                (
                    "mode_counts",
                    (("causal_cav", 0), ("zoh_fallback", 2),
                     ("sensor_fixed_bypass", 1)),
                ),
                ("frame_counts", (("WORLD", 1), ("SENSOR_FIXED", 2))),
            ):
                with self.subTest(official_field=field):
                    forged_statistics = replace(result.statistics)
                    object.__setattr__(forged_statistics, field, value)
                    with self.assertRaises(module.FunctionalAssayError):
                        module.FunctionalAssayResult(
                            result.views, forged_statistics
                        )

    def test_direct_result_rejects_forged_event_populations(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)

            duplicate_geometry = list(result.geometry)
            duplicate_geometry[1] = replace(
                duplicate_geometry[1], event_id=duplicate_geometry[0].event_id
            )
            duplicate_geometry_tuple = tuple(duplicate_geometry)
            duplicate_views = tuple(replace(
                view, geometry=duplicate_geometry_tuple
            ) for view in result.views)
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "geometry event IDs are not exactly contiguous",
            ):
                module.FunctionalAssayResult(
                    duplicate_views, result.statistics
                )

            duplicate_sidecar = list(result.retire_sidecar)
            duplicate_sidecar[1] = replace(
                duplicate_sidecar[1], event_id=duplicate_sidecar[0].event_id
            )
            retired_view = replace(
                result.views[2], transport_sidecar=tuple(duplicate_sidecar)
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "sidecar event IDs are not exactly contiguous",
            ):
                module.FunctionalAssayResult(
                    result.views[:2] + (retired_view,), result.statistics
                )

            shortened = result.geometry[:-1]
            shortened_views = tuple(
                replace(view, geometry=shortened) for view in result.views
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError, "geometry cardinality"
            ):
                module.FunctionalAssayResult(
                    shortened_views, result.statistics
                )

    def test_nested_records_and_builtin_subclasses_fail_structurally(self):
        class IntegerSubclass(int):
            pass

        class TextSubclass(str):
            pass

        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)

            mutated_dual = replace(result.retire_sidecar[0].dual_time)
            object.__setattr__(
                mutated_dual, "latency_ns", mutated_dual.latency_ns + 2
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "DualTimeEvent validation failed",
            ):
                replace(result.retire_sidecar[0], dual_time=mutated_dual)

            subclass_dual = replace(
                result.retire_sidecar[0].dual_time,
                semantics_label=TextSubclass(TRANSPORT_TIME_SEMANTICS),
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "transport semantics must be exact str",
            ):
                replace(result.retire_sidecar[0], dual_time=subclass_dual)

            world = result.geometry[0]
            subclass_grid = replace(
                world.world_grid,
                coordinate_convention=TextSubclass(
                    world.world_grid.coordinate_convention
                ),
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "coordinate convention must be exact str",
            ):
                replace(world, world_grid=subclass_grid)

            with self.assertRaisesRegex(
                module.FunctionalAssayError, "view_name must be exact str"
            ):
                replace(
                    result.views[0],
                    view_name=TextSubclass(module.RAW_CAV_VIEW),
                )
            with self.assertRaises(module.FunctionalAssayError):
                replace(
                    result.statistics,
                    grid_width=IntegerSubclass(module.GRID_WIDTH),
                )
            subclass_modes = (
                (TextSubclass("causal_cav"), 1),
                ("zoh_fallback", 1),
                ("sensor_fixed_bypass", 1),
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError, "mode_counts keys/order differ"
            ):
                replace(result.statistics, mode_counts=subclass_modes)
            with self.assertRaisesRegex(
                module.FunctionalAssayError,
                "statistics transport semantics differ",
            ):
                replace(
                    result.statistics,
                    transport_time_semantics=TextSubclass(
                        TRANSPORT_TIME_SEMANTICS
                    ),
                )

    def test_replay_validator_rejects_signed_zero_and_coherent_alternate_ray(self):
        source, outcomes = _synthetic_inputs()
        with _synthetic_authority():
            result = module.run_functional_assay(source, outcomes)
            self.assertIs(
                module.validate_functional_assay_result(
                    result, source, outcomes
                ),
                result,
            )

            original_ray = result.geometry[0].ray_xyz
            signed_ray = list(original_ray)
            zero_index = next(
                index for index, value in enumerate(signed_ray) if value == 0.0
            )
            signed_ray[zero_index] = (
                -0.0
                if math.copysign(1.0, signed_ray[zero_index]) > 0.0
                else 0.0
            )
            signed_result = _coherent_geometry_result(
                result, 0, tuple(signed_ray)
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError, "differs from exact input replay"
            ):
                module.validate_functional_assay_result(
                    signed_result, source, outcomes
                )

            alternate = _coherent_geometry_result(
                result, 0, (0.0, 0.0, 1.0)
            )
            with self.assertRaisesRegex(
                module.FunctionalAssayError, "differs from exact input replay"
            ):
                module.validate_functional_assay_result(
                    alternate, source, outcomes
                )

    def test_core_is_python38_and_has_no_io_or_forbidden_imports(self):
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 8))
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        forbidden = ("scorer", "evaluator", "reference", "selector")
        self.assertFalse(any(
            token in name.lower() for name in imported for token in forbidden
        ))
        self.assertFalse(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
            for node in ast.walk(tree)
        ))
        self.assertFalse(any(name in imported for name in ("os", "pathlib")))
        clean = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import benchmarks.redred_cluster2_cav_bridge.functional_assay "
                    "as assay; assert tuple(assay._run_geometry.__annotations__) "
                    "== ('source', 'return')"
                ),
            ],
            cwd=path.parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)


@unittest.skipUnless(
    os.environ.get("REDRED_RUN_CLUSTER2_FUNCTIONAL_ASSAY_OFFICIAL") == "1",
    "set REDRED_RUN_CLUSTER2_FUNCTIONAL_ASSAY_OFFICIAL=1 for the official smoke",
)
class OfficialFunctionalAssaySmoke(unittest.TestCase):
    def test_official_8503_join_modes_grid_and_transport(self):
        dataset = os.environ.get("REDRED_UZH_SHAPES_ROTATION_ROOT")
        cyclemask = os.environ.get("REDRED_CLUSTER2_CYCLEMASK_PATH")
        if not dataset or not cyclemask:
            self.fail("official dataset and cyclemask environment paths are required")
        repository_root = Path(module.__file__).parents[2]
        source = build_official_uzh_functional_source(
            Path(dataset), Path(cyclemask)
        )
        outcomes = load_abaa094_native_outcomes(repository_root)
        result = module.run_functional_assay(source, outcomes)
        self.assertIs(
            module.validate_functional_assay_result(
                result, source, outcomes
            ),
            result,
        )

        statistics = result.statistics
        self.assertEqual(statistics.event_count, 8_503)
        self.assertEqual(statistics.pose_count, 11_883)
        self.assertEqual(statistics.exact_join_count, 8_503)
        self.assertEqual(statistics.decision_count, 8_503)
        self.assertEqual(
            statistics.mode_counts,
            (("causal_cav", 8_420), ("zoh_fallback", 0),
             ("sensor_fixed_bypass", 83)),
        )
        self.assertEqual(
            statistics.frame_counts, (("WORLD", 8_420), ("SENSOR_FIXED", 83))
        )
        self.assertEqual(
            statistics.latency_histogram, ((1, 6_393), (2, 2_077), (3, 33))
        )
        self.assertEqual(statistics.grid_quantized_count, 8_420)
        self.assertEqual(statistics.grid_unique_count, 821)
        self.assertEqual(
            (statistics.grid_x_min, statistics.grid_x_max), (238, 298)
        )
        self.assertEqual(
            (statistics.grid_y_min, statistics.grid_y_max), (93, 165)
        )
        self.assertEqual(
            (statistics.grid_index_min, statistics.grid_index_max),
            (47_876, 84_754),
        )
        self.assertEqual(
            statistics.join_identity_sha256,
            "bfbd23b607cc7d68371133e7d67da43c2302641391b4cdeac572013eaab256b2",
        )
        self.assertEqual(
            statistics.retire_sidecar_sha256,
            "c29d9b980674da62d48e3a4cb0dc26618d08a3658997a7a5e90eb15ef81b6897",
        )
        self.assertEqual(
            statistics.grid_sha256,
            "f5cb124031b2a343b55a85f92902bd8b764bc865298d9de58ee86f60e49048e0",
        )
        self.assertEqual(
            len(set(view.geometry_sha256 for view in result.views)), 1
        )
        self.assertTrue(all(
            view.geometry is result.geometry for view in result.views
        ))
        for digest in (
            statistics.join_identity_sha256,
            statistics.geometry_sha256,
            statistics.retire_sidecar_sha256,
            statistics.grid_sha256,
        ):
            self.assertRegex(digest, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
