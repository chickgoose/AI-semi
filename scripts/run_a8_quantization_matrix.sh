#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULT_ROOT="${A8_MATRIX_OUT:-/tmp/a8-quantization-matrix}"
MANIFEST_DIR="$RESULT_ROOT/manifests"

mkdir -p "$RESULT_ROOT" "$MANIFEST_DIR"
python3 "$PROJECT_ROOT/tests/a8_age_calendar_wheel/generate_scaling_manifests.py" \
  --output-dir "$MANIFEST_DIR"

for source_count in ${A8_MATRIX_NS:-16 32 64}; do
  manifest="$MANIFEST_DIR/manifest.a8-scaling-n$source_count.json"
  trace_dir="$RESULT_ROOT/traces-n$source_count"
  for architecture in ${A8_MATRIX_ARCHES:-rr exact b1 b2 b4 b8}; do
    printf 'A8_MATRIX_RUN n=%s architecture=%s\n' \
      "$source_count" "$architecture"
    arch=wheel
    bucket_cycles=1
    epoch_count=$((2 * source_count))
    case "$architecture" in
      rr) arch=rr ;;
      exact) arch=exact ;;
      b1) bucket_cycles=1 ;;
      b2) bucket_cycles=2 ;;
      b4) bucket_cycles=4 ;;
      b8) bucket_cycles=8 ;;
      *)
        printf 'unsupported matrix architecture: %s\n' "$architecture" >&2
        exit 2
        ;;
    esac
    if [[ "$arch" == wheel ]]; then
      epoch_count=$((2 * source_count / bucket_cycles))
    fi
    A8_NUM_SOURCES="$source_count" \
    A8_ARCH="$arch" \
    A8_BUCKET_CYCLES="$bucket_cycles" \
    A8_EPOCH_COUNT="$epoch_count" \
    A8_CANDIDATE_NAME="a8-$architecture-n$source_count" \
    A8_TRACE_OUT="$trace_dir" \
    A8_CLEAN_OUT="$RESULT_ROOT/$architecture-n$source_count" \
      "$PROJECT_ROOT/scripts/run_a8_age_calendar_wheel.sh" \
      --manifest "$manifest"
  done
done

printf 'A8 quantization matrix complete: %s\n' "$RESULT_ROOT"
