#!/usr/bin/env python3
"""Generate deterministic presentation SVGs from the sealed official result."""

from __future__ import annotations

import argparse
from html import escape
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Dict, Mapping, Optional, Sequence


SCHEMA = "redred.cluster2_cav_bridge.official_uzh_functional_result/v1"
STATUS = "COMPLETE_SCOPED_OBSERVATIONAL_AND_SOFTWARE_RESULT_WITH_HOLDS"
SEAL_ALGORITHM = "SHA256_CANONICAL_JSON_EXCLUDING_SEAL"
DEFAULT_RECEIPT = (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_result.json"
)
DEFAULT_OUTPUT_DIRECTORY = "docs/presentation/assets"
OUTPUT_NAMES = (
    "cluster2_cav_population_flow.svg",
    "cluster2_cav_latency_histogram.svg",
    "cluster2_cav_world_grid_coverage.svg",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PresentationAssetError(ValueError):
    """The sealed input or requested presentation output is invalid."""


def _fail(message: str) -> None:
    raise PresentationAssetError(message)


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        )
        return (encoded + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise PresentationAssetError("value is not canonical JSON") from error


def _exact_json(value: object, where: str = "receipt") -> None:
    if type(value) is dict:
        for key, child in value.items():  # type: ignore[union-attr]
            if type(key) is not str:
                _fail("%s key is not exact str" % where)
            _exact_json(child, "%s.%s" % (where, key))
        return
    if type(value) is list:
        for index, child in enumerate(value):  # type: ignore[union-attr]
            _exact_json(child, "%s[%d]" % (where, index))
        return
    if type(value) not in (str, int, bool):
        _fail("%s has an unsupported or non-exact JSON type" % where)


def _mapping(value: object, fields: Sequence[str], where: str) -> Mapping[str, object]:
    if type(value) is not dict or frozenset(value) != frozenset(fields):
        _fail("%s fields differ" % where)
    return value  # type: ignore[return-value]


def _integer(value: object, where: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail("%s must be an exact integer >= %d" % (where, minimum))
    return value


def _text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        _fail("%s must be a non-empty exact str" % where)
    return value


def _sha256(value: object, where: str) -> str:
    result = _text(value, where)
    if _SHA256.fullmatch(result) is None:
        _fail("%s must be a lowercase full SHA-256" % where)
    return result


def load_official_result(path: Path) -> Mapping[str, object]:
    """Read canonical sealed JSON and validate every field used by a figure."""

    if not isinstance(path, Path):
        _fail("receipt path must be pathlib.Path")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PresentationAssetError("cannot read official result") from error
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PresentationAssetError("official result is not ASCII JSON") from error
    _exact_json(value)
    if _canonical_bytes(value) != raw:
        _fail("official result is not exact canonical JSON")
    top = _mapping(value, (
        "schema", "status", "input_authority", "population", "latency",
        "world_grid", "digests", "three_view_equality", "claim_scope", "seal",
    ), "official result")
    if top["schema"] != SCHEMA or top["status"] != STATUS:
        _fail("official result schema/status differs")

    seal = _mapping(top["seal"], ("algorithm", "sha256"), "seal")
    if seal["algorithm"] != SEAL_ALGORITHM:
        _fail("seal algorithm differs")
    body = dict(top)
    body.pop("seal")
    expected_seal = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if not hmac.compare_digest(_sha256(seal["sha256"], "seal SHA"), expected_seal):
        _fail("official result seal differs")

    population = _mapping(top["population"], (
        "events", "poses", "exact_native_join", "decisions", "causal_cav",
        "zoh_fallback", "sensor_fixed_bypass", "native_overrun",
    ), "population")
    counts = {name: _integer(population[name], "population.%s" % name)
              for name in population}
    if (
        counts["events"] != counts["exact_native_join"]
        or counts["events"] != counts["decisions"]
        or counts["events"] != counts["causal_cav"]
        + counts["zoh_fallback"] + counts["sensor_fixed_bypass"]
    ):
        _fail("population conservation differs")

    latency = _mapping(top["latency"], (
        "semantics", "native_clock_period_ps", "histogram_cycles", "event_count",
    ), "latency")
    if _text(latency["semantics"], "latency semantics") != (
        "TRANSPORT_LATENCY_INJECTION_NOT_PHYSICAL_REPLAY"
    ):
        _fail("latency semantics differs")
    clock_ps = _integer(latency["native_clock_period_ps"], "native clock", 1)
    latency_count = _integer(latency["event_count"], "latency event count")
    histogram = latency["histogram_cycles"]
    if type(histogram) is not list or not histogram:
        _fail("latency histogram must be a non-empty exact list")
    prior = -1
    histogram_total = 0
    for index, row in enumerate(histogram):
        if type(row) is not list or len(row) != 2:
            _fail("latency histogram row %d differs" % index)
        cycle = _integer(row[0], "latency cycle", 1)
        count = _integer(row[1], "latency count", 1)
        if cycle <= prior:
            _fail("latency histogram cycles are not strictly increasing")
        prior = cycle
        histogram_total += count
    if latency_count != counts["events"] or histogram_total != latency_count:
        _fail("latency histogram population differs")
    if clock_ps % 1000 != 0:
        _fail("native clock cannot be labeled in exact integer nanoseconds")

    grid = _mapping(top["world_grid"], (
        "width", "height", "input_frame", "excluded_frame", "quantized_count",
        "excluded_sensor_fixed_count", "unique_cell_count", "x_range_inclusive",
        "y_range_inclusive", "index_range_inclusive", "coordinate_convention",
    ), "world grid")
    width = _integer(grid["width"], "grid width", 1)
    height = _integer(grid["height"], "grid height", 1)
    quantized = _integer(grid["quantized_count"], "grid quantized count", 1)
    excluded = _integer(
        grid["excluded_sensor_fixed_count"], "grid excluded count"
    )
    unique = _integer(grid["unique_cell_count"], "grid unique count", 1)
    if grid["input_frame"] != "WORLD" or grid["excluded_frame"] != "SENSOR_FIXED":
        _fail("grid frame scope differs")
    if quantized + excluded != counts["events"] or unique > quantized:
        _fail("grid population differs")
    for name, limit in (("x_range_inclusive", width),
                        ("y_range_inclusive", height),
                        ("index_range_inclusive", width * height)):
        span = grid[name]
        if type(span) is not list or len(span) != 2:
            _fail("%s differs" % name)
        lower = _integer(span[0], "%s minimum" % name)
        upper = _integer(span[1], "%s maximum" % name)
        if lower > upper or upper >= limit:
            _fail("%s is outside the grid" % name)
    _text(grid["coordinate_convention"], "coordinate convention")

    claims = _mapping(top["claim_scope"], (
        "actual_native_rtl_observation", "software_cav_replay",
        "world_functional_mapping", "latency_quality", "wire_complete_cav_rtl",
        "rtl_ppa",
    ), "claim scope")
    if claims["latency_quality"] != (
        "HOLD_OBSERVATIONAL_LATENCY_SIDECAR_ONLY_NOT_PHYSICAL_REPLAY_OR_QUALITY"
    ) or claims["world_functional_mapping"] != (
        "PASS_SOFTWARE_WORLD_RAY_GRID_MAPPING_ONLY_NOT_RTL"
    ):
        _fail("figure claim scope differs")

    digests = _mapping(top["digests"], (
        "join_identity_sha256", "geometry_sha256", "retire_sidecar_sha256",
        "world_grid_sha256",
    ), "digests")
    for name, digest in digests.items():
        _sha256(digest, "digests.%s" % name)
    equality = _mapping(top["three_view_equality"], (
        "view_order", "geometry_sha256_by_view", "all_geometry_digests_equal",
        "shared_geometry_object",
    ), "three-view equality")
    expected_order = ["RAW-CAV", "AER-OCC-CAV", "AER-RET-CAV"]
    if equality["view_order"] != expected_order:
        _fail("three-view order differs")
    view_rows = equality["geometry_sha256_by_view"]
    if type(view_rows) is not list or len(view_rows) != len(expected_order):
        _fail("three-view digest rows differ")
    for index, row in enumerate(view_rows):
        checked = _mapping(row, ("view", "sha256"), "view digest row")
        if (
            checked["view"] != expected_order[index]
            or _sha256(checked["sha256"], "view geometry SHA")
            != digests["geometry_sha256"]
        ):
            _fail("three-view geometry digest differs")
    if (
        equality["all_geometry_digests_equal"] is not True
        or equality["shared_geometry_object"] is not True
    ):
        _fail("three-view equality claim differs")
    return top


def _svg_document(width: int, height: int, title: str, content: str) -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="title desc">
<title id="title">%s</title>
<desc id="desc">Generated only from the sealed official Cluster2 CAV functional result.</desc>
<style>
text{font-family:Arial,sans-serif;fill:#172033}.title{font-size:28px;font-weight:700}.subtitle{font-size:15px;fill:#526075}.label{font-size:18px;font-weight:700}.value{font-size:30px;font-weight:700}.small{font-size:14px;fill:#526075}.axis{stroke:#8793a6;stroke-width:1}.box{fill:#f4f7fb;stroke:#526075;stroke-width:2}.world{fill:#dff5ed;stroke:#16856b;stroke-width:2}.bypass{fill:#fff0dc;stroke:#c97512;stroke-width:2}.accent{fill:#3167d5}.muted{fill:#a9b6c8}.hold{fill:#fff8e8;stroke:#c98a16;stroke-width:1.5}.arrow{stroke:#526075;stroke-width:2;fill:none;marker-end:url(#arrow)}
</style>
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#526075"/></marker></defs>
%s
</svg>
""" % (width, height, width, height, escape(title), content)


def _population_svg(receipt: Mapping[str, object]) -> str:
    population = receipt["population"]
    equality = receipt["three_view_equality"]
    assert isinstance(population, dict) and isinstance(equality, dict)
    content = """
<text x="50" y="55" class="title">Population flow</text>
<text x="50" y="82" class="subtitle">Official inputs → exact native join → software CAV decisions</text>
<rect x="50" y="130" width="235" height="150" rx="12" class="box"/>
<text x="75" y="165" class="label">Official UZH source</text>
<text x="75" y="210" class="value">%s events</text>
<text x="75" y="242" class="small">%s poses</text>
<path d="M285 205 H365" class="arrow"/>
<rect x="365" y="130" width="235" height="150" rx="12" class="box"/>
<text x="390" y="165" class="label">Native identity join</text>
<text x="390" y="210" class="value">%s joined</text>
<text x="390" y="242" class="small">%s overrun</text>
<path d="M600 205 H680" class="arrow"/>
<rect x="680" y="110" width="225" height="105" rx="12" class="world"/>
<text x="705" y="145" class="label">WORLD</text>
<text x="705" y="184" class="value">%s</text>
<rect x="680" y="235" width="225" height="105" rx="12" class="bypass"/>
<text x="705" y="270" class="label">SENSOR_FIXED</text>
<text x="705" y="309" class="value">%s</text>
<rect x="955" y="130" width="195" height="150" rx="12" class="box"/>
<text x="980" y="165" class="label">Three views</text>
<text x="980" y="202" class="small">RAW-CAV</text>
<text x="980" y="228" class="small">AER-OCC-CAV</text>
<text x="980" y="254" class="small">AER-RET-CAV</text>
<text x="50" y="400" class="label">Mode partition</text>
<text x="50" y="435" class="small">causal CAV %s  •  ZOH fallback %s  •  sensor-fixed bypass %s</text>
<text x="50" y="472" class="small">All three views share one geometry digest: %s</text>
""" % (
        population["events"], population["poses"],
        population["exact_native_join"], population["native_overrun"],
        population["causal_cav"] + population["zoh_fallback"],
        population["sensor_fixed_bypass"], population["causal_cav"],
        population["zoh_fallback"], population["sensor_fixed_bypass"],
        "yes" if equality["all_geometry_digests_equal"] else "no",
    )
    return _svg_document(1200, 520, "Cluster2 CAV population flow", content)


def _latency_svg(receipt: Mapping[str, object]) -> str:
    latency = receipt["latency"]
    assert isinstance(latency, dict)
    histogram = latency["histogram_cycles"]
    assert isinstance(histogram, list)
    maximum = max(row[1] for row in histogram)
    bars = []
    base_y = 410
    scale = 270.0 / maximum
    slot_width = 850.0 / len(histogram)
    bar_width = min(150.0, slot_width * 0.55)
    for index, row in enumerate(histogram):
        x = 100.0 + index * slot_width + (slot_width - bar_width) / 2.0
        cycles, count = row
        height = count * scale
        top = base_y - height
        nanoseconds = cycles * latency["native_clock_period_ps"] // 1000
        bars.append(
            '<rect x="%s" y="%s" width="%s" height="%s" rx="6" class="accent"/>'
            '<text x="%s" y="%s" text-anchor="middle" class="value">%s</text>'
            '<text x="%s" y="445" text-anchor="middle" class="label">%s cycle%s</text>'
            '<text x="%s" y="470" text-anchor="middle" class="small">%s ns sidecar</text>'
            % (_number(x), _number(top), _number(bar_width), _number(height),
               _number(x + bar_width / 2.0), _number(top - 12), count,
               _number(x + bar_width / 2.0), cycles,
               "" if cycles == 1 else "s", _number(x + bar_width / 2.0),
               nanoseconds)
        )
    content = """
<text x="50" y="55" class="title">Native latency histogram</text>
<text x="50" y="82" class="subtitle">%s events • %s ps native clock</text>
<line x1="100" y1="410" x2="950" y2="410" class="axis"/>
%s
<rect x="50" y="500" width="900" height="50" rx="8" class="hold"/>
<text x="500" y="531" text-anchor="middle" class="small">Observational latency sidecar only — not physical replay or latency quality</text>
""" % (latency["event_count"], latency["native_clock_period_ps"], "\n".join(bars))
    return _svg_document(1000, 580, "Cluster2 native latency histogram", content)


def _number(value: float) -> str:
    return ("%.3f" % value).rstrip("0").rstrip(".")


def _grid_svg(receipt: Mapping[str, object]) -> str:
    grid = receipt["world_grid"]
    assert isinstance(grid, dict)
    width = grid["width"]
    height = grid["height"]
    x_min, x_max = grid["x_range_inclusive"]
    y_min, y_max = grid["y_range_inclusive"]
    canvas_x, canvas_y, canvas_width, canvas_height = 70, 125, 768, 384
    sx = canvas_width / width
    sy = canvas_height / height
    range_x = canvas_x + x_min * sx
    range_y = canvas_y + y_min * sy
    range_width = (x_max - x_min + 1) * sx
    range_height = (y_max - y_min + 1) * sy
    content = """
<text x="50" y="55" class="title">Software world-grid coverage</text>
<text x="50" y="82" class="subtitle">512 × 256 equirectangular grid • WORLD rows only</text>
<rect x="%s" y="%s" width="%s" height="%s" fill="#f4f7fb" stroke="#526075" stroke-width="2"/>
<rect x="%s" y="%s" width="%s" height="%s" fill="#6fcbb2" fill-opacity="0.65" stroke="#16856b" stroke-width="2"/>
<text x="875" y="150" class="label">WORLD mapping</text>
<text x="875" y="193" class="value">%s rows</text>
<text x="875" y="226" class="small">%s unique cells</text>
<text x="875" y="278" class="label">Inclusive ranges</text>
<text x="875" y="310" class="small">x: %s–%s</text>
<text x="875" y="338" class="small">y: %s–%s</text>
<text x="875" y="366" class="small">index: %s–%s</text>
<text x="875" y="420" class="label">Excluded</text>
<text x="875" y="452" class="small">%s SENSOR_FIXED rows</text>
<text x="70" y="548" class="small">Highlighted area is the reported inclusive bounding range, not a per-cell occupancy plot.</text>
<text x="70" y="577" class="small">Scope: software WORLD ray/grid mapping only — not RTL.</text>
""" % (
        canvas_x, canvas_y, canvas_width, canvas_height,
        _number(range_x), _number(range_y), _number(range_width),
        _number(range_height), grid["quantized_count"],
        grid["unique_cell_count"], x_min, x_max, y_min, y_max,
        grid["index_range_inclusive"][0], grid["index_range_inclusive"][1],
        grid["excluded_sensor_fixed_count"],
    )
    return _svg_document(1200, 620, "Cluster2 software world-grid coverage", content)


def render_assets(receipt: Mapping[str, object]) -> Mapping[str, bytes]:
    """Return the exact three deterministic SVG payloads."""

    return {
        OUTPUT_NAMES[0]: _population_svg(receipt).encode("utf-8"),
        OUTPUT_NAMES[1]: _latency_svg(receipt).encode("utf-8"),
        OUTPUT_NAMES[2]: _grid_svg(receipt).encode("utf-8"),
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".%s." % path.name, dir=str(path.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, str(path))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as error:
        raise PresentationAssetError("cannot write SVG asset") from error


def generate(receipt_path: Path, output_directory: Path) -> Mapping[str, bytes]:
    receipt = load_official_result(receipt_path)
    assets = render_assets(receipt)
    for name in OUTPUT_NAMES:
        _atomic_write(output_directory / name, assets[name])
    return assets


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=root / DEFAULT_RECEIPT)
    parser.add_argument(
        "--output-directory", type=Path, default=root / DEFAULT_OUTPUT_DIRECTORY
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    generate(arguments.receipt, arguments.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
