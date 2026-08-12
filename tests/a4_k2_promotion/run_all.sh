#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$(mktemp -d /tmp/a4-k2-promotion.XXXXXX)
trap 'rm -rf -- "$work"' EXIT

python3 -B -m unittest -v \
  "$here/test_export.py" \
  "$here/test_owner_materialization.py"
python3 -B "$here/run_promotion_replay.py" \
  --a1-repo /home/chickgoose/projects/a1 \
  --a2-repo /home/chickgoose/projects/a2 \
  --a3-repo /home/chickgoose/projects/a3 \
  --a4-repo "$repo" \
  --work-dir "$work/work" \
  --output "$work/promotion-replay.json"

python3 -B - "$work/promotion-replay.json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["qualification"] == "OWNER_RTL_TRANSACTION_REPLAY_PASS"
assert report["suite_run_counts"] == {"full50": 50, "capacity22": 22, "directed": 1}
assert len(report["owners"]) == 3
assert [owner["owner"] for owner in report["owners"]] == ["a2", "a3", "a4"]
assert all(owner["run_count"] == 73 for owner in report["owners"])
assert report["provenance"]["a2"]["commit"] == "d74ff962aaf07c5209f1a1d1c69832735c654a0d"
assert report["provenance"]["a3"]["commit"] == "bd1c1ee955685fc077afe930116a03bc49a8218f"
assert report["provenance"]["a4"]["commit"] == "0e613b6933f1bb92e9b2f75b79a50663187f17d3"
assert all(report["provenance"][owner]["source_origin"] == "exact_git_commit_object"
           for owner in ("a2", "a3", "a4"))
assert report["mutation_kills"] == [
    "malicious_trace",
    "malicious_generation_index",
    "malicious_occurrence_time_shift_rehashed",
    "malicious_driver_time_index_shift",
]
print("A4_K2_PROMOTION_SELF_CHECK_PASS")
PY
