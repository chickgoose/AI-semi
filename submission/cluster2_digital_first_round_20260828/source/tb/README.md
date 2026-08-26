# Standalone functional replay

패키지 루트에서 실행한다. `ADDRPOL_FILE`과 `LEDGER_FILE`은 TB plusarg다.

## Xcelium

```bash
xrun -64bit -sv -timescale 1ns/1ps \
  -top redred_cluster2_polarity_v1_native_observational_tb \
  -f source/tb/polarity_v1_tb.f \
  +ADDRPOL_FILE=source/testdata/uzh_shapes_rotation_patch.addrpol.txt \
  +LEDGER_FILE=/tmp/polarity_v1_raw_native_ledger.psv

python3 source/tb/polarity_native_ledger.py \
  source/testdata/uzh_shapes_rotation_patch.addrpol.txt \
  /tmp/polarity_v1_raw_native_ledger.psv
```

독립 verifier 출력은 `generated=8503`, `delivered=8503`, `overrun=0`,
`phantom=0`, `duplicate=0`, `drain_empty=true`여야 한다.

`run_polarity_v1_native_observational.py`는 원래 통합 저장소에서 upstream
Git checkout provenance까지 검사하는 공식 runner의 고정 사본이다. 독립 제출
디렉터리에서는 위 filelist 명령이 self-contained replay 경로다.

