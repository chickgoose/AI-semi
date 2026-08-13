#!/usr/bin/env python3
"""Stream a validated 10 ns common-activity VCD onto the exact 5 ns timeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import BinaryIO


SCHEMA = "k2_w3_vcd_timestamp_scale_v1"
PRODUCER_ID = "k2-w3-vcd-timestamp-scaler-v1"
NUMERATOR = 1
DENOMINATOR = 2
MAX_LINE_BYTES = 1024 * 1024
MAX_TIMESTAMP_DIGITS = 30
TIMESTAMP = re.compile(rb"#[0-9]+")
DIRECTIVE = re.compile(r"\$[A-Za-z][A-Za-z0-9_]*")
SCALAR_CHANGE = re.compile(rb"[01xXzZ][!-~]+")
VECTOR_CHANGE = re.compile(rb"[bB][01xXzZ]+[ \t]+[!-~]+")
REAL_CHANGE = re.compile(
    rb"[rR][+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    rb"[ \t]+[!-~]+"
)
HEADER_DIRECTIVES = {
    "comment", "date", "enddefinitions", "scope", "timescale",
    "timezero", "upscope", "var", "version",
}
SIMULATION_DIRECTIVES = {"comment", "dumpall", "dumpoff", "dumpon", "dumpvars"}
VALUE_DIRECTIVES = SIMULATION_DIRECTIVES - {"comment"}
VALIDATION_FIELDS = {
    "candidate", "vcd_sha256", "window_start_tick_1ps", "window_end_tick_1ps",
    "duration_tick_1ps", "benchmark_measurement_cycles",
    "activity_window_ref_cycles", "window_contract", "scope",
}
SHA256 = re.compile(r"[0-9a-f]{64}")


class ScalingError(Exception):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, *, include_leaf: bool) -> None:
    absolute = _absolute(path)
    parts = absolute.parts
    current = Path(parts[0])
    limit = len(parts) if include_leaf else len(parts) - 1
    for part in parts[1:limit]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as error:
            raise ScalingError(f"path component does not exist: {current}") from error
        if stat.S_ISLNK(mode):
            raise ScalingError(f"symlink path component is forbidden: {current}")
        if current != absolute and not stat.S_ISDIR(mode):
            raise ScalingError(f"non-directory path component: {current}")


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _open_stable_regular(path: Path) -> tuple[BinaryIO, os.stat_result]:
    absolute = _absolute(path)
    _reject_symlink_components(absolute, include_leaf=True)
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        descriptor = os.open(absolute, flags)
    except OSError as error:
        raise ScalingError(f"cannot open input as a non-symlink file: {absolute}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ScalingError(f"input is not a regular file: {absolute}")
        return os.fdopen(descriptor, "rb", buffering=1024 * 1024), before
    except Exception:
        os.close(descriptor)
        raise


def _verify_stable(path: Path, stream: BinaryIO, before: os.stat_result) -> None:
    after_fd = os.fstat(stream.fileno())
    try:
        after_path = _absolute(path).lstat()
    except OSError as error:
        raise ScalingError("input pathname changed during scaling") from error
    expected = _stat_identity(before)
    if _stat_identity(after_fd) != expected or _stat_identity(after_path) != expected:
        raise ScalingError("input changed during scaling")


def _validate_destination(path: Path) -> Path:
    absolute = _absolute(path)
    _reject_symlink_components(absolute, include_leaf=False)
    try:
        absolute.lstat()
    except FileNotFoundError:
        return absolute
    raise ScalingError(f"refusing to overwrite destination: {absolute}")


def _new_staging_file(destination: Path) -> tuple[int, Path]:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    )
    return descriptor, Path(name)


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _hash_regular(path: Path) -> str:
    digest = hashlib.sha256()
    stream, before = _open_stable_regular(path)
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        _verify_stable(path, stream, before)
    finally:
        stream.close()
    return digest.hexdigest()


def _read_stable_small(path: Path, limit: int = 64 * 1024) -> tuple[bytes, str]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    stream, before = _open_stable_regular(path)
    try:
        while True:
            chunk = stream.read(min(8192, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ScalingError("validation receipt exceeds the size limit")
            chunks.append(chunk)
            digest.update(chunk)
        _verify_stable(path, stream, before)
    finally:
        stream.close()
    return b"".join(chunks), digest.hexdigest()


def _validation_receipt(path: Path) -> dict[str, object]:
    raw, digest = _read_stable_small(path)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise ScalingError("validation receipt must be ASCII") from error
    if not text.endswith("\n") or "\r" in text:
        raise ScalingError("validation receipt must use canonical LF records")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            raise ScalingError("malformed validation receipt record")
        key, value = line.split("=", 1)
        if not key or not value or key in fields:
            raise ScalingError("duplicate or empty validation receipt field")
        fields[key] = value
    if set(fields) != VALIDATION_FIELDS:
        raise ScalingError("validation receipt field set mismatch")
    if not SHA256.fullmatch(fields["vcd_sha256"]):
        raise ScalingError("validation receipt VCD SHA-256 is malformed")
    try:
        start = int(fields["window_start_tick_1ps"])
        end = int(fields["window_end_tick_1ps"])
        duration = int(fields["duration_tick_1ps"])
        benchmark_cycles = int(fields["benchmark_measurement_cycles"])
        activity_cycles = int(fields["activity_window_ref_cycles"])
    except ValueError as error:
        raise ScalingError("validation receipt has a noninteger count") from error
    if (start < 0 or end <= start or duration != end - start or
            benchmark_cycles <= 0 or activity_cycles != benchmark_cycles + 1 or
            duration != activity_cycles * 10000):
        raise ScalingError("validation receipt does not prove the 10 ns activity window")
    if fields["window_contract"] != "frozen_measurement_active_edges_plus_final_service":
        raise ScalingError("validation receipt window contract mismatch")
    if not fields["candidate"] or not fields["scope"]:
        raise ScalingError("validation receipt identity is empty")
    return {
        "candidate": fields["candidate"],
        "duration_tick_1ps": duration,
        "format": "k2_w3_rebased_activity_sha256_text_v1",
        "scope": fields["scope"],
        "sha256": digest,
        "size_bytes": len(raw),
        "vcd_sha256": fields["vcd_sha256"],
    }


class HeaderState:
    def __init__(self) -> None:
        self.open_directive: str | None = None
        self.timescale_payload: list[str] = []
        self.timescale_count = 0
        self.enddefinitions_count = 0
        self.timezero_count = 0

    @property
    def definitions_complete(self) -> bool:
        return self.enddefinitions_count == 1

    def _finish(self) -> None:
        assert self.open_directive is not None
        name = self.open_directive
        payload = self.timescale_payload
        if name == "timescale":
            if self.definitions_complete:
                raise ScalingError("timescale occurs after enddefinitions")
            self.timescale_count += 1
            if self.timescale_count != 1 or payload not in (["1ps"], ["1", "ps"]):
                raise ScalingError("VCD must contain exactly one 1 ps timescale")
        elif name == "enddefinitions":
            if payload:
                raise ScalingError("malformed enddefinitions directive")
            self.enddefinitions_count += 1
            if self.enddefinitions_count != 1 or self.timescale_count != 1:
                raise ScalingError("enddefinitions requires one preceding 1 ps timescale")
        elif name == "timezero":
            if self.definitions_complete:
                raise ScalingError("timezero occurs after enddefinitions")
            self.timezero_count += 1
            if self.timezero_count != 1 or payload != ["0"]:
                raise ScalingError("only one zero-valued timezero is permitted")
        self.open_directive = None
        self.timescale_payload = []

    def consume(self, text: str) -> None:
        tokens = text.split()
        # After enddefinitions, '$' is also a legal one-character VCD
        # identifier.  Only a leading '$' token can begin a directive; a '$'
        # following a vector value is data, not syntax.
        if self.open_directive is None and (not tokens or not tokens[0].startswith("$")):
            if tokens and not self.definitions_complete:
                raise ScalingError("text outside a VCD header directive")
            return
        for index, token in enumerate(tokens):
            if self.open_directive is not None:
                if token == "$end":
                    self._finish()
                    if index != len(tokens) - 1:
                        raise ScalingError("tokens follow a VCD directive terminator")
                elif self.open_directive in {"timescale", "enddefinitions", "timezero"}:
                    self.timescale_payload.append(token)
                    if len(self.timescale_payload) > 2:
                        raise ScalingError(f"malformed {self.open_directive} directive")
                elif self.open_directive in VALUE_DIRECTIVES:
                    raise ScalingError("dump directive value records must use separate lines")
                continue
            if token == "$end":
                raise ScalingError("unmatched VCD $end")
            if token.startswith("$"):
                if not DIRECTIVE.fullmatch(token):
                    raise ScalingError("malformed VCD directive")
                name = token[1:]
                permitted = (HEADER_DIRECTIVES if not self.definitions_complete
                             else SIMULATION_DIRECTIVES)
                if name not in permitted:
                    raise ScalingError(f"unsupported VCD ${name} directive")
                self.open_directive = name
                self.timescale_payload = []
            elif not self.definitions_complete:
                raise ScalingError("text outside a VCD header directive")

    def finish_file(self) -> None:
        if self.open_directive is not None:
            raise ScalingError(f"unterminated VCD ${self.open_directive} directive")
        if self.timescale_count != 1 or self.enddefinitions_count != 1:
            raise ScalingError("VCD header is incomplete")


def _line_parts(raw_line: bytes) -> tuple[bytes, bytes]:
    if raw_line.endswith(b"\r\n"):
        return raw_line[:-2], b"\r\n"
    if raw_line.endswith(b"\n"):
        return raw_line[:-1], b"\n"
    return raw_line, b""


def _validate_value_change(content: bytes) -> None:
    if not (SCALAR_CHANGE.fullmatch(content) or
            VECTOR_CHANGE.fullmatch(content) or
            REAL_CHANGE.fullmatch(content)):
        raise ScalingError("malformed or unsupported VCD value change")


def _scale_stream(input_path: Path, output: BinaryIO) -> dict[str, object]:
    input_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    header = HeaderState()
    count = 0
    first: int | None = None
    last: int | None = None
    output_size = 0
    stream, before = _open_stable_regular(input_path)
    try:
        while True:
            raw_line = stream.readline(MAX_LINE_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > MAX_LINE_BYTES:
                raise ScalingError("VCD line exceeds the size limit")
            input_hash.update(raw_line)
            content, ending = _line_parts(raw_line)
            try:
                text = content.decode("ascii")
            except UnicodeDecodeError as error:
                raise ScalingError("VCD must contain ASCII bytes only") from error

            outside_directive = header.open_directive is None
            looks_like_timestamp = content.lstrip().startswith(b"#")
            if outside_directive and looks_like_timestamp:
                if not header.definitions_complete:
                    raise ScalingError("timestamp occurs before enddefinitions")
                if not TIMESTAMP.fullmatch(content):
                    raise ScalingError("malformed VCD timestamp")
                digits = content[1:]
                if len(digits) > MAX_TIMESTAMP_DIGITS:
                    raise ScalingError("VCD timestamp has too many digits")
                timestamp = int(digits)
                if last is not None and timestamp < last:
                    raise ScalingError("VCD timestamps are not monotonic")
                if timestamp % DENOMINATOR:
                    raise ScalingError("VCD timestamp is not exactly divisible by 2")
                if first is None:
                    first = timestamp
                last = timestamp
                count += 1
                emitted = f"#{timestamp // DENOMINATOR}".encode("ascii") + ending
            else:
                value_directive_record = (
                    header.open_directive in VALUE_DIRECTIVES and content != b"$end"
                )
                if value_directive_record:
                    _validate_value_change(content)
                elif (outside_directive and header.definitions_complete and content and
                      not content.startswith(b"$")):
                    _validate_value_change(content)
                if not value_directive_record:
                    header.consume(text)
                emitted = raw_line
            output.write(emitted)
            output_hash.update(emitted)
            output_size += len(emitted)

        header.finish_file()
        if count < 2 or first != 0 or last is None or last <= 0:
            raise ScalingError("VCD must span a positive timeline beginning at timestamp zero")
        _verify_stable(input_path, stream, before)
    finally:
        stream.close()

    return {
        "input_sha256": input_hash.hexdigest(),
        "input_size_bytes": before.st_size,
        "output_sha256": output_hash.hexdigest(),
        "output_size_bytes": output_size,
        "timestamp_count": count,
        "input_first": first,
        "input_last": last,
        "output_first": first // DENOMINATOR,
        "output_last": last // DENOMINATOR,
    }


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_inode(left: Path, right: Path) -> bool:
    left_stat, right_stat = left.lstat(), right.lstat()
    return (left_stat.st_dev, left_stat.st_ino) == (right_stat.st_dev, right_stat.st_ino)


def _unlink_if_staged(destination: Path, staged: Path) -> None:
    try:
        if _same_inode(destination, staged):
            destination.unlink()
    except OSError:
        pass


def _publish_pair(output_temp: Path, output: Path, output_sha256: str,
                  receipt_temp: Path, receipt: Path, receipt_sha256: str) -> None:
    _validate_destination(output)
    _validate_destination(receipt)
    if (_hash_regular(output_temp) != output_sha256 or
            _hash_regular(receipt_temp) != receipt_sha256):
        raise ScalingError("staged output changed before publication")
    receipt_linked = False
    output_linked = False
    try:
        os.link(receipt_temp, receipt, follow_symlinks=False)
        receipt_linked = True
        os.link(output_temp, output, follow_symlinks=False)
        output_linked = True
        if (not _same_inode(output, output_temp) or
                not _same_inode(receipt, receipt_temp) or
                _hash_regular(output) != output_sha256 or
                _hash_regular(receipt) != receipt_sha256):
            raise ScalingError("published artifacts do not match staged bytes")
        for parent in {output.parent, receipt.parent}:
            _fsync_directory(parent)
    except BaseException as error:
        if output_linked:
            _unlink_if_staged(output, output_temp)
        if receipt_linked:
            _unlink_if_staged(receipt, receipt_temp)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(error, ScalingError):
            raise
        raise ScalingError("could not publish output and receipt without overwrite") from error
    finally:
        output_temp.unlink(missing_ok=True)
        receipt_temp.unlink(missing_ok=True)


def scale(input_path: Path, validation_path: Path,
          output_path: Path, receipt_path: Path,
          numerator: int, denominator: int) -> None:
    if (numerator, denominator) != (NUMERATOR, DENOMINATOR):
        raise ScalingError("this converter permits only the exact reduced scale 1/2")
    input_absolute = _absolute(input_path)
    validation_absolute = _absolute(validation_path)
    output_absolute = _validate_destination(output_path)
    receipt_absolute = _validate_destination(receipt_path)
    if len({input_absolute, validation_absolute, output_absolute, receipt_absolute}) != 4:
        raise ScalingError("input, validation, output, and receipt paths must be distinct")
    validation = _validation_receipt(validation_absolute)
    producer_path = Path(__file__)
    producer_sha256 = _hash_regular(producer_path)

    output_fd, output_temp = _new_staging_file(output_absolute)
    receipt_temp: Path | None = None
    try:
        with os.fdopen(output_fd, "wb", buffering=1024 * 1024) as output_stream:
            metrics = _scale_stream(input_absolute, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if metrics["input_sha256"] != validation["vcd_sha256"]:
            raise ScalingError("input VCD does not match its validation receipt")
        if metrics["input_last"] != validation["duration_tick_1ps"]:
            raise ScalingError("input VCD duration does not match its validation receipt")

        document: dict[str, object] = {
            "input": {
                "role": "validated_10ns_common_activity_vcd",
                "sha256": metrics["input_sha256"],
                "size_bytes": metrics["input_size_bytes"],
            },
            "output": {
                "role": "exact_5ns_common_activity_vcd",
                "sha256": metrics["output_sha256"],
                "size_bytes": metrics["output_size_bytes"],
            },
            "producer": {
                "id": PRODUCER_ID,
                "sha256": producer_sha256,
            },
            "schema": SCHEMA,
            "source_validation": validation,
            "timestamps": {
                "count": metrics["timestamp_count"],
                "input_first": metrics["input_first"],
                "input_last": metrics["input_last"],
                "output_first": metrics["output_first"],
                "output_last": metrics["output_last"],
            },
            "transform": {
                "denominator": DENOMINATOR,
                "input_clock_period_ps": 10000,
                "input_timescale": "1 ps",
                "kind": "integer_timestamp_ratio",
                "numerator": NUMERATOR,
                "output_clock_period_ps": 5000,
                "output_timescale": "1 ps",
                "ratio": "1/2",
                "rounding": "reject_non_integral",
            },
        }
        if _hash_regular(producer_path) != producer_sha256:
            raise ScalingError("converter changed during scaling")
        receipt_bytes = _canonical_json(document)
        receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
        receipt_fd, receipt_temp = _new_staging_file(receipt_absolute)
        with os.fdopen(receipt_fd, "wb") as receipt_stream:
            receipt_stream.write(receipt_bytes)
            receipt_stream.flush()
            os.fsync(receipt_stream.fileno())
        _publish_pair(output_temp, output_absolute, str(metrics["output_sha256"]),
                      receipt_temp, receipt_absolute, receipt_sha256)
        receipt_temp = None
    finally:
        output_temp.unlink(missing_ok=True)
        if receipt_temp is not None:
            receipt_temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--validation-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--numerator", required=True, type=int)
    parser.add_argument("--denominator", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        scale(args.input, args.validation_receipt, args.output, args.receipt,
              args.numerator, args.denominator)
    except (ScalingError, OSError, ValueError) as error:
        print(f"VCD timestamp scaling failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
