#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$root"
V="${VERILATOR:-/tmp/a7-toolchain/usr/bin/verilator}"
Y="${YOSYS:-/tmp/a7-toolchain/usr/bin/yosys}"
out="${W2_STAGING_TEST_OUT:-$(mktemp -d /dev/shm/w2-staging.XXXXXX)}"
[[ -d "$out" && -z "$(ls -A "$out")" ]] || { echo "output must be a new empty directory: $out" >&2; exit 2; }
python3 -m unittest -v tests.w2_physical_staging.test_manifest |& tee "$out/manifest.log"
flags=(--binary --timing --assert -Wall -Wno-fatal -Wno-BLKSEQ -Wno-SYNCASYNCNET
 -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD
 -Wno-UNOPTFLAT -Wno-DECLFILENAME -Wno-MULTITOP)
run_case(){ local s=$1 p=$2 d="$out/$s-$p" top extras model=(); mkdir "$d"
  [[ $p == gsclib045 ]] && model=(-DW2_P6_TEST_ONLY tests/w2_p6_techmap/gsclib045_test_models.sv)
  case $s in
   fovea) top=w2_fovea_owner_vs_staged_tb; extras=(rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv); expected='W2_FOVEA_STAGING_LOCKSTEP_PASS checks=1767 accept=163 retire=163';;
   a2) top=w2_a2_owner_vs_staged_tb; extras=(rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_adapter.sv rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6_top.sv); expected='W2_A2_STAGING_LOCKSTEP_PASS checks=706 accept=182 link=182 retire=182 stalls=0';;
   a3) top=w2_a3_owner_vs_staged_tb; extras=(rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_adapter.sv rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6_top.sv); expected='W2_A3_STAGING_LOCKSTEP_PASS checks=674 accept=115 retire=115 stalls=0';;
  esac
  "$V" "${flags[@]}" --top-module "$top" --Mdir "$d/obj" -o sim \
    -f "rtl/technology/physical_staging/filelists/${s}_${p}.f" "${model[@]}" \
    "${extras[@]}" "tests/w2_physical_staging/${s}_owner_vs_staged_tb.sv" >"$d/build.log" 2>&1
  timeout 30s "$d/obj/sim" >"$d/run.log" 2>&1
  grep -Fxq "$expected" "$d/run.log"; echo "$s $p $expected"
}
for p in generic gsclib045; do for s in fovea a2 a3; do run_case "$s" "$p"; done; done

ylib="${LD_LIBRARY_PATH:-}"; [[ -d /tmp/a7-toolchain/usr/lib/x86_64-linux-gnu ]] && ylib="/tmp/a7-toolchain/usr/lib/x86_64-linux-gnu${ylib:+:$ylib}"
for s in fovea a2 a3; do
  top="w2_${s}_p6_physical_staging_top"; [[ $s == fovea ]] && top=w2_fovea_r1_physical_staging_top
  sources=$(sed -n '3,$p' "rtl/technology/physical_staging/filelists/${s}_gsclib045.f" | tr '\n' ' ')
  env LD_LIBRARY_PATH="$ylib" "$Y" -Q -p "read_verilog -sv -Irtl/technology/p6 -DW2_P6_TECH_GSCLIB045 $sources; read_verilog -sv -lib -DW2_P6_TEST_ONLY tests/w2_p6_techmap/gsclib045_test_models.sv; hierarchy -check -top $top; proc; flatten; opt; write_json $out/$s.json" >"$out/$s-yosys.log" 2>&1
done
python3 - "$out" <<'PY'
import json, pathlib, sys
from tests.w2_physical_staging.endpoint_inventory import validate_yosys_json
o=pathlib.Path(sys.argv[1])
cases={'fovea':('w2_fovea_r1_physical_staging_top','r1',{'TLATNTSCAX2':1,'MX2X1':2,'DFFRHQX1':2,'DFFNSRX1':5}),
 'a2':('w2_a2_p6_physical_staging_top','p6',{'TLATNTSCAX2':1,'MX2X1':5,'DFFRHQX1':5,'DFFNSRX1':12}),
 'a3':('w2_a3_p6_physical_staging_top','p6',{'TLATNTSCAX2':1,'MX2X1':5,'DFFRHQX1':5,'DFFNSRX1':12})}
for key,args in cases.items(): print(key,validate_yosys_json(json.loads((o/f'{key}.json').read_text()),*args))
PY
echo "W2_PHYSICAL_STAGING_ALL_PASS output=$out"
