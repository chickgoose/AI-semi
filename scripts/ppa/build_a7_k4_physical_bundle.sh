#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || {
  printf 'usage: %s <empty-output-directory>\n' "$0" >&2
  exit 2
}
bundle_dir="$1"
[[ ! -e "$bundle_dir" ]] || {
  printf 'bundle path already exists: %s\n' "$bundle_dir" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
commit="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
mkdir -p "$bundle_dir/common/constraints" "$bundle_dir/sources"

for design in prefix-k4 replicated-k4; do
  destination="$bundle_dir/sources/$design"
  mkdir -p "$destination"
  git -C "$PROJECT_ROOT" archive "$commit" \
    rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv \
    rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv \
    rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv \
    tests/a7_parallel_event_compactor/a7_structural_wrappers.sv \
    tb/filelists/a7_k4_structural.f | tar -x -C "$destination"
done

cp "$PROJECT_ROOT/constraints/aer_common.sdc" \
  "$bundle_dir/common/constraints/aer_common.sdc"
cp "$PROJECT_ROOT/scripts/drivers/extract_genus_metrics.sh" \
  "$bundle_dir/common/extract_genus_metrics.sh"
cp "$PROJECT_ROOT/scripts/ppa/parse_a23_genus_detail.sh" \
  "$bundle_dir/common/parse_genus_detail.sh"
cp "$SCRIPT_DIR/a7_k4_genus_compare.tcl" \
  "$bundle_dir/common/a7_k4_genus_compare.tcl"
cp "$SCRIPT_DIR/run_a7_k4_genus_comparison.sh" \
  "$bundle_dir/run_comparison.sh"

{
  printf 'design\tcommit\ttop\tfilelist\n'
  printf 'prefix-k4\t%s\ta7_prefix_structural_top\ttb/filelists/a7_k4_structural.f\n' "$commit"
  printf 'replicated-k4\t%s\ta7_replicated_structural_top\ttb/filelists/a7_k4_structural.f\n' "$commit"
} > "$bundle_dir/designs.tsv"

chmod +x "$bundle_dir/run_comparison.sh" \
  "$bundle_dir/common/extract_genus_metrics.sh" \
  "$bundle_dir/common/parse_genus_detail.sh"

(
  cd "$bundle_dir"
  find . -type f ! -name bundle-files.sha256 -exec sha256sum {} \; | sort \
    > bundle-files.sha256
)
printf 'built A7 K4 physical bundle: %s\n' "$bundle_dir"
