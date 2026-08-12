#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IVERILOG_BIN="${IVERILOG:-iverilog}"
VVP_BIN="${VVP:-vvp}"
VERILATOR_BIN="${VERILATOR:-verilator}"
OUT_DIR="${AER_K2_BINDING_TEST_OUT:-/tmp/aer-k2-binding-compile-test}"

for tool in "$IVERILOG_BIN" "$VVP_BIN" "$VERILATOR_BIN"; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

mkdir -p "$OUT_DIR"
cd "$PROJECT_ROOT"

"$IVERILOG_BIN" -g2012 -Wall -s k2_binding_pkg_compile_tb \
  -o "$OUT_DIR/pkg.vvp" \
  rtl/common/aer_k2_binding_pkg.sv \
  tests/a1_k2_common_binding/k2_binding_pkg_compile_tb.sv
"$VVP_BIN" "$OUT_DIR/pkg.vvp" | tee "$OUT_DIR/pkg.log"
grep -q 'K2_BINDING_PKG_COMPILE_PASS' "$OUT_DIR/pkg.log"

"$IVERILOG_BIN" -g2012 -Wall -s k2_ordered_link_compile_tb \
  -o "$OUT_DIR/link.vvp" -f tests/a1_k2_common_binding/files.f
"$VVP_BIN" "$OUT_DIR/link.vvp" | tee "$OUT_DIR/link.log"
grep -q 'K2_ORDERED_LINK_COMPILE_PASS' "$OUT_DIR/link.log"

"$VERILATOR_BIN" --lint-only --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-SYNCASYNCNET -Wno-UNUSEDPARAM \
  --top-module k2_ordered_link_compile_tb \
  -f tests/a1_k2_common_binding/files.f \
  tests/a1_k2_common_binding/k2_binding_pkg_compile_tb.sv

printf 'K2_COMMON_BINDING_COMPILE_TESTS_PASS\n'
