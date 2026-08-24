"""Fail-closed checks for the scoped local official replay receipt."""

from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import re
import unittest

from benchmarks.redred_cluster2_cav_bridge.contract import canonical_json_bytes
from benchmarks.redred_cluster2_cav_bridge import official_functional_run


ROOT = Path(__file__).parents[2]
RESULT_RELATIVE = (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_result.json"
)
RECEIPT_RELATIVE = (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_replay_receipt.json"
)
LOG_RELATIVE = (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_replay_receipt.log"
)
RESULT = ROOT / RESULT_RELATIVE
RECEIPT = ROOT / RECEIPT_RELATIVE
LOG = ROOT / LOG_RELATIVE

SCHEMA = (
    "redred.cluster2_cav_bridge.official_uzh_functional_replay_receipt/v1"
)
STATUS = "PASS_LOCAL_EXACT_GOLDEN_REPLAY_NOT_SIGNED_OR_HARDWARE_ATTESTATION"
SEAL_ALGORITHM = "SHA256_CANONICAL_JSON_EXCLUDING_SEAL"
TEST_ID = (
    "tests.redred_cluster2_cav_bridge.test_official_functional_run."
    "EnvironmentGatedOfficialGoldenReplay."
    "test_exact_official_replay_matches_committed_golden"
)
EXPECTED_LOG = (
    "SANITIZED_UNITTEST_LOG v1\n"
    "test_exact_official_replay_matches_committed_golden "
    "(tests.redred_cluster2_cav_bridge.test_official_functional_run."
    "EnvironmentGatedOfficialGoldenReplay."
    "test_exact_official_replay_matches_committed_golden) ... ok\n"
    "----------------------------------------------------------------------\n"
    "Ran 1 test\n"
    "\n"
    "OK\n"
    "sanitization=ELAPSED_SECONDS_AND_LOCAL_INPUT_PATH_VALUES_OMITTED\n"
).encode("ascii")

EXPECTED_COMMAND = {
    "argv": [
        "python3", "-B", "-m", "unittest", TEST_ID, "-v",
    ],
    "environment": [
        {
            "name": "REDRED_RUN_CLUSTER2_FUNCTIONAL_ASSAY_OFFICIAL",
            "value": "1",
        },
        {
            "name": "REDRED_UZH_SHAPES_ROTATION_ROOT",
            "value": "official_uzh/shapes_rotation",
        },
        {
            "name": "REDRED_CLUSTER2_CYCLEMASK_PATH",
            "value": (
                "replay_inputs/"
                "uzh_shapes_rotation_patch.cyclemask.lf.txt"
            ),
        },
    ],
    "path_semantics": (
        "LOCAL_CLI_PATH_VALUES_SANITIZED_TO_HASH_BOUND_RELATIVE_AUTHORITY_LABELS"
    ),
    "test_semantics": "REEXECUTE_AND_REQUIRE_EXACT_EQUALITY_TO_OFFICIAL_RESULT",
    "working_directory": "repository_root",
}

EXPECTED_RUNTIME = {
    "dont_write_bytecode": True,
    "implementation": "cpython",
    "python38_runtime_qualification": "HOLD_NOT_EXECUTED_ON_PYTHON_3_8",
    "version": [3, 14, 4],
}

EXPECTED_CLAIMS = {
    "cav_rtl": "HOLD_NOT_IMPLEMENTED_OR_REPLAYED",
    "hardware_attestation": "HOLD_NOT_HARDWARE_ATTESTED",
    "performance": "HOLD_NO_QUALIFIED_PERFORMANCE_CLAIM",
    "python38_runtime": "HOLD_NOT_EXECUTED_ON_PYTHON_3_8",
    "receipt_signature": "HOLD_UNSIGNED_PUBLIC_LOCAL_OBSERVATION",
    "rtl_ppa": "HOLD_NOT_EVALUATED",
    "software_exact_golden_replay": "PASS_LOCAL_EXACT_EQUALITY_ONLY",
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReplayReceiptError(ValueError):
    """A replay receipt, bound artifact, or claim is inconsistent."""


def _fail(message):
    raise ReplayReceiptError(message)


def _exact_json_tree(value, where="receipt"):
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _fail("%s key must be exact str" % where)
            _exact_json_tree(child, "%s.%s" % (where, key))
        return
    if type(value) is list:
        for index, child in enumerate(value):
            _exact_json_tree(child, "%s[%d]" % (where, index))
        return
    if type(value) not in (str, int, bool):
        _fail("%s contains a non-exact JSON value" % where)


def _typed_equal(value, expected, where):
    if type(value) is not type(expected):
        _fail("%s type differs" % where)
    if type(expected) is dict:
        if frozenset(value) != frozenset(expected):
            _fail("%s fields differ" % where)
        for key, child in expected.items():
            _typed_equal(value[key], child, "%s.%s" % (where, key))
    elif type(expected) is list:
        if len(value) != len(expected):
            _fail("%s length differs" % where)
        for index, child in enumerate(expected):
            _typed_equal(value[index], child, "%s[%d]" % (where, index))
    elif value != expected:
        _fail("%s value differs" % where)


def _sha256(value, where):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("%s must be a lowercase SHA-256" % where)
    return value


def _relative_path(value, where):
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        _fail("%s must be a relative POSIX path" % where)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        _fail("%s must be a normalized relative POSIX path" % where)
    return value


def _artifact(path, label):
    if path.is_symlink() or not path.is_file():
        _fail("%s must be a regular non-symlink file" % label)
    payload = path.read_bytes()
    return {
        "path": label,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _reseal(value):
    body = copy.deepcopy(value)
    body.pop("seal")
    value["seal"]["sha256"] = hashlib.sha256(
        canonical_json_bytes(body)
    ).hexdigest()


def validate_replay_receipt(value):
    """Validate structure, seal, local log, and current official authority."""

    _exact_json_tree(value)
    if type(value) is not dict or frozenset(value) != frozenset((
        "schema", "status", "input_authority", "command", "runtime",
        "observation", "claim_scope", "seal",
    )):
        _fail("top-level fields differ")
    receipt = value
    if receipt["schema"] != SCHEMA or receipt["status"] != STATUS:
        _fail("schema/status differs")
    _typed_equal(receipt["command"], EXPECTED_COMMAND, "command")
    _typed_equal(receipt["runtime"], EXPECTED_RUNTIME, "runtime")
    _typed_equal(receipt["claim_scope"], EXPECTED_CLAIMS, "claim scope")

    authority = receipt["input_authority"]
    if type(authority) is not dict or frozenset(authority) != frozenset((
        "official_result", "official_sources", "cyclemask_lf",
    )):
        _fail("input authority fields differ")
    result_row = authority["official_result"]
    if type(result_row) is not dict or frozenset(result_row) != frozenset((
        "path", "sha256", "size_bytes", "seal_algorithm", "seal_sha256",
    )):
        _fail("official result authority fields differ")
    _relative_path(result_row["path"], "official result path")
    _sha256(result_row["sha256"], "official result digest")
    _sha256(result_row["seal_sha256"], "official result seal")

    result_raw = RESULT.read_bytes()
    try:
        official = json.loads(result_raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ReplayReceiptError("official result is not ASCII JSON") from error
    if canonical_json_bytes(official) != result_raw:
        _fail("official result is not canonical")
    official_functional_run.validate_official_functional_summary(official, ROOT)
    expected_result = _artifact(RESULT, RESULT_RELATIVE)
    expected_result.update({
        "seal_algorithm": official["seal"]["algorithm"],
        "seal_sha256": official["seal"]["sha256"],
    })
    _typed_equal(result_row, expected_result, "current official result")
    _typed_equal(
        authority["official_sources"],
        official["input_authority"]["official_sources"],
        "official external sources",
    )
    cyclemask = official["input_authority"]["cyclemask"]
    expected_cyclemask = {
        "line_endings": "LF",
        "path": cyclemask["path"],
        "sha256": cyclemask["observed_raw_sha256"],
        "size_bytes": cyclemask["observed_size_bytes"],
    }
    _typed_equal(authority["cyclemask_lf"], expected_cyclemask, "LF cyclemask")

    observation = receipt["observation"]
    expected_log = _artifact(LOG, LOG_RELATIVE)
    expected_observation = {
        "errors": 0,
        "exit_code": 0,
        "failures": 0,
        "log": expected_log,
        "outcome": "OK",
        "sanitization": (
            "RETAIN_TEST_ID_OUTCOME_COUNT_AND_EXIT;"
            "OMIT_ELAPSED_SECONDS_AND_LOCAL_INPUT_PATH_VALUES"
        ),
        "skipped": 0,
        "test_id": TEST_ID,
        "tests_run": 1,
    }
    _typed_equal(observation, expected_observation, "test observation")
    if LOG.read_bytes() != EXPECTED_LOG:
        _fail("sanitized unittest log bytes differ")

    for path_value, where in (
        (result_row["path"], "official result path"),
        (authority["cyclemask_lf"]["path"], "cyclemask path"),
        (observation["log"]["path"], "log path"),
        (receipt["command"]["environment"][1]["value"], "dataset label"),
        (receipt["command"]["environment"][2]["value"], "cyclemask label"),
    ):
        _relative_path(path_value, where)
    serialized = canonical_json_bytes(receipt) + LOG.read_bytes()
    for forbidden in (b"/tmp/", b"/home/", b"/Users/", b"file://"):
        if forbidden in serialized:
            _fail("receipt/log contains a private host path")

    seal = receipt["seal"]
    if type(seal) is not dict or frozenset(seal) != frozenset((
        "algorithm", "sha256",
    )):
        _fail("seal fields differ")
    if seal["algorithm"] != SEAL_ALGORITHM:
        _fail("seal algorithm differs")
    supplied = _sha256(seal["sha256"], "receipt seal")
    body = dict(receipt)
    body.pop("seal")
    expected_seal = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if not hmac.compare_digest(supplied, expected_seal):
        _fail("receipt seal differs")
    return receipt


class OfficialFunctionalReplayReceiptTests(unittest.TestCase):
    def setUp(self):
        self.raw = RECEIPT.read_bytes()
        self.receipt = json.loads(self.raw.decode("ascii"))

    def test_canonical_seal_log_and_current_official_authority(self):
        self.assertEqual(self.raw, canonical_json_bytes(self.receipt))
        self.assertIs(validate_replay_receipt(self.receipt), self.receipt)
        self.assertEqual(LOG.read_bytes(), EXPECTED_LOG)

    def test_exact_types_fail_closed(self):
        class IntSubclass(int):
            pass

        class StrSubclass(str):
            pass

        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        mutations = []
        changed = copy.deepcopy(self.receipt)
        changed["observation"]["exit_code"] = False
        mutations.append(changed)
        changed = copy.deepcopy(self.receipt)
        changed["observation"]["tests_run"] = IntSubclass(1)
        mutations.append(changed)
        changed = copy.deepcopy(self.receipt)
        changed["status"] = StrSubclass(STATUS)
        mutations.append(changed)
        changed = copy.deepcopy(self.receipt)
        changed["command"]["argv"] = ListSubclass(changed["command"]["argv"])
        mutations.append(changed)
        mutations.append(DictSubclass(copy.deepcopy(self.receipt)))
        for changed in mutations:
            with self.subTest(changed_type=type(changed).__name__):
                with self.assertRaises(ReplayReceiptError):
                    validate_replay_receipt(changed)

    def test_semantic_and_authority_mutations_fail_even_when_resealed(self):
        mutations = []
        for path, replacement in (
            (("status",), "PASS"),
            (("input_authority", "official_result", "sha256"), "0" * 64),
            (("input_authority", "official_result", "seal_sha256"), "1" * 64),
            (("input_authority", "official_sources", 0, "size_bytes"), 1),
            (("input_authority", "cyclemask_lf", "sha256"), "2" * 64),
            (("command", "argv", 4), "different.test"),
            (("runtime", "python38_runtime_qualification"), "PASS"),
            (("observation", "exit_code"), 1),
            (("observation", "log", "sha256"), "3" * 64),
            (("claim_scope", "cav_rtl"), "PASS"),
        ):
            changed = copy.deepcopy(self.receipt)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            _reseal(changed)
            mutations.append(changed)
        for changed in mutations:
            with self.subTest(status=changed["status"]):
                with self.assertRaises(ReplayReceiptError):
                    validate_replay_receipt(changed)

        changed = copy.deepcopy(self.receipt)
        changed["seal"]["sha256"] = "f" * 64
        with self.assertRaises(ReplayReceiptError):
            validate_replay_receipt(changed)

    def test_scope_paths_and_python38_grammar_are_explicit(self):
        self.assertEqual(self.receipt["status"], STATUS)
        self.assertEqual(
            self.receipt["runtime"]["python38_runtime_qualification"],
            "HOLD_NOT_EXECUTED_ON_PYTHON_3_8",
        )
        self.assertTrue(all(
            value.startswith("HOLD_")
            for key, value in self.receipt["claim_scope"].items()
            if key != "software_exact_golden_replay"
        ))
        source = Path(__file__).read_text("utf-8")
        ast.parse(source, filename=str(Path(__file__)), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
