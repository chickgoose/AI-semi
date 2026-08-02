#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { printf 'usage: %s <empty-output-directory>\n' "$0" >&2; exit 2; }
bundle_dir="$1"
[[ ! -e "$bundle_dir" ]] || { printf 'bundle path already exists: %s\n' "$bundle_dir" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
mkdir -p "$bundle_dir/common" "$bundle_dir/sources"

extract_paths() {
  local commit="$1"
  local destination="$2"
  shift 2
  mkdir -p "$destination"
  git -C "$PROJECT_ROOT" archive "$commit" "$@" | tar -x -C "$destination"
}

extract_paths 9c0d044 "$bundle_dir/sources/baseline" \
  rtl/common rtl/baseline tb/filelists/baseline.f
extract_paths 856b7f9 "$bundle_dir/sources/a2-round-robin" \
  rtl/common rtl/baseline/aer_tx.sv rtl/baseline/aer_rx.sv \
  rtl/experiments/a2_round_robin tb/filelists/a2_round_robin.f
extract_paths c8f422d "$bundle_dir/sources/a3-bubble-free" \
  rtl/common rtl/baseline/fixed_priority_arbiter.sv rtl/baseline/aer_rx.sv \
  rtl/experiments/a3_bubble_free tb/filelists/a3_bubble_free.f
extract_paths 57d17e6 "$bundle_dir/sources/a23-ee430" \
  rtl/common rtl/baseline/aer_rx.sv rtl/experiments/a23_ee430 \
  tb/filelists/a23_ee430.f
extract_paths 9c0d044 "$bundle_dir/common/constraints" constraints/aer_common.sdc

cp "$PROJECT_ROOT/scripts/drivers/extract_genus_metrics.sh" \
  "$bundle_dir/common/extract_genus_metrics.sh"
cp "$SCRIPT_DIR/genus_a23_compare.tcl" "$bundle_dir/common/genus_a23_compare.tcl"
cp "$SCRIPT_DIR/parse_a23_genus_detail.sh" "$bundle_dir/common/parse_a23_genus_detail.sh"
cp "$SCRIPT_DIR/run_a23_genus_comparison.sh" "$bundle_dir/run_comparison.sh"
cp "$SCRIPT_DIR/a23_designs.tsv" "$bundle_dir/designs.tsv"
chmod +x "$bundle_dir/run_comparison.sh" \
  "$bundle_dir/common/extract_genus_metrics.sh" \
  "$bundle_dir/common/parse_a23_genus_detail.sh"

printf 'built A23 Genus comparison bundle: %s\n' "$bundle_dir"
