#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$(mktemp -d /tmp/a4-k2-promotion.XXXXXX)
trap 'rm -rf -- "$work"' EXIT

python3 -B -m unittest -v "$here/test_export.py"
python3 -B "$here/run_promotion_replay.py" \
  --a1-repo /home/chickgoose/projects/a1 \
  --a2-repo /home/chickgoose/projects/a2 \
  --a3-repo /home/chickgoose/projects/a3 \
  --work-dir "$work/work" \
  --output "$work/promotion-replay.json"

python3 -B - "$work/promotion-replay.json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["qualification"] == "OWNER_RTL_TRANSACTION_REPLAY_PASS"
assert report["suite_run_counts"] == {"full50": 50, "capacity22": 22, "directed": 1}
assert len(report["owners"]) == 2
assert all(owner["run_count"] == 73 for owner in report["owners"])
assert report["mutation_kills"] == [
    "malicious_trace",
    "malicious_generation_index",
    "malicious_occurrence_time_shift_rehashed",
    "malicious_driver_time_index_shift",
]
print("A4_K2_PROMOTION_SELF_CHECK_PASS")
PY
