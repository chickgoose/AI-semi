# Ganghee Cluster2 PPA 진단 인수인계

기준일: 2026-08-24  
판정: **서버 관측 screening 자료; release-bound native PPA authority는 HOLD**

이 문서는 현수가 1차 발표용 물리 증거를 패키징할 때 원본 top과 확장 top을
혼동하지 않도록 현재 서버 자료를 분리한 인수인계다. 아래 report 자체는 아직
공개 integration branch에 봉인되지 않았고 서버 Git worktree에서도 untracked다.
따라서 수치는 diagnostic으로만 사용하며 최종 release PASS로 승격하지 않는다.

## 1. Pinned 원본 `cluster2_steal_buf`

Top:

`aer_tx16_trad_rowcol_fovea_cluster2_steal_buf`

RTL SHA-256:

`56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96`

이 RTL은 public native authority가 고정한 Ganghee commit
`5ac1f0e3c0e6991558afa699e64680f708ff625d`의 원본과 일치한다.

| 항목 | Genus mapped screening | Innovus |
| --- | ---: | ---: |
| clock | 2.0 ns / 500 MHz | 없음 |
| mapped area | 700.074, 364 cells | **HOLD** |
| setup slack | +0.224 ns | **HOLD** |
| total power | 0.127932 mW | **HOLD** |
| DRC / antenna | N/A | **HOLD** |

Genus power breakdown:

| 구성 | mW |
| --- | ---: |
| internal | 0.102296 |
| switching | 0.0256211 |
| leakage | 0.0000149427 |
| total | 0.127932 |

조건:

- Genus `23.14-s090_1`
- GPDK045 `slow_vdd1v0_basicCells.lib`, `PVT_0P9V_125C`
- enclosed wireload pre-layout screening
- input/output delay 각각 0.2 ns
- clock uncertainty와 output load는 script에서 미설정
- VCD/SAIF 없는 vectorless estimate
- clock-gating insertion 요청, 실제 gated FF 0/48
- 500 MHz single-point setup PASS이지 swept/exact Fmax가 아님

Ganghee server `semi-ai` repository 기준 상대경로:

- `rtl/ganghee_cluster2/arbiter2.v`
- `rtl/ganghee_cluster2/arbiter4_tree.v`
- `rtl/ganghee_cluster2_steal/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v`
- `synth/run_genus_steal_buf_baseline.tcl`
- `reports_steal_buf_baseline/area.rpt`
- `reports_steal_buf_baseline/timing.rpt`
- `reports_steal_buf_baseline/power.rpt`
- `reports_steal_buf_baseline/clock_gating.rpt`

핵심 SHA-256:

| 대상 | SHA-256 |
| --- | --- |
| RTL | `56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96` |
| Genus script | `b7a34768851fcb3c2e00c35b0ecbace40799a1b62a4f55ddc5d249ae88841e7b` |
| area report | `2b4322c9823c0477ddd86ad1348560e27b1b43b91b39aaac55fa475c0cd2371b` |
| timing report | `4020c31eff52b747f5a6f9d502bde0c86eacc176bb11c900d0e0a9d817fc19a8` |
| power report | `b4af9e9a26adf606945ddcf5fe6d605910ab95df2069f3a05844d27dc5968502` |

## 2. 별도 polarity-extended server implementation

Top:

`aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity`

RTL SHA-256:

`20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81`

이 top은 pinned 원본에 다음을 추가한 다른 구현이다.

- `polarity_in[15:0]`
- `pol_mask0/1[3:0]`
- source별 2-slot polarity FIFO
- 원본 48 FF와 달리 mapped top 총 88 FF

| 단계 | period | frequency | area / instances | setup | hold | vectorless power | DRC / antenna |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Genus clean point | 3.5 ns | 285.714 MHz | 1156.644 / 544 | +1.125 ns | 미보고 | 0.0505898 mW | N/A |
| Innovus fastest clean observed | 3.5 ns | 285.714 MHz | 1254.114 / 596 | +0.454 ns | +0.167 ns | 0.10738887 mW | 0 / 0 |
| Innovus first faster fail | 3.0 ns | 333.333 MHz | 1261.980 / 599 | -0.004 ns | +0.169 ns | 0.12577530 mW | 0 / 0 |

3.5 ns post-route power breakdown:

| 구성 | mW |
| --- | ---: |
| internal | 0.07647953 |
| switching | 0.03088277 |
| leakage | 0.00002657 |
| total | 0.10738887 |

관측 sweep bracket은 `[285.714, 333.333) MHz`지만 정식 Fmax가 아니다.

P&R 조건:

- Genus `23.14-s090_1`, Innovus `23.14-s088_1`
- GPDK045 slow 0.9 V / 125°C
- `rc_typical`, `gpdk045.tch`
- setup/hold 동일 `view_slow`
- clock uncertainty 0.100 ns
- input/output delay 0.250 ns, output load 0.010
- aspect 1.0, target utilization 0.5, margins 10
- activity file 없음; primary/sequential default activity 0.2
- MMMC Non-OCV, SI off, report상 No SPEF/RCDB
- fillers/decaps 0
- ideal-clock warning 1, no-drive warning 34
- report 생성 뒤 `write_db`가 `IMPIMEX-7043`으로 실패하고 TCL catch됨

Ganghee server `redred-faer` repository 기준 상대경로:

- `rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v`
- `syn/pnr/resynth_steal_buf_polarity/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_3.5.sdc`
- `syn/pnr/resynth_steal_buf_polarity/genus_3.5.tcl`
- `syn/pnr/resynth_steal_buf_polarity/mmmc_3.5.tcl`
- `syn/pnr/resynth_steal_buf_polarity/run_3.5.tcl`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_area.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_gtiming.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_gpower.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_pnr_area.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_pnr_power.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_setup_timing.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_hold_timing.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_check_timing.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_drc.rpt`
- `syn/pnr/resynth_steal_buf_polarity/*_3.5_antenna.rpt`
- `syn/pnr/resynth_steal_buf_polarity/innovus_3.5.log`

## 3. 발표 규칙

말해도 되는 문장:

> pinned 원본 `cluster2_steal_buf`는 2.0 ns Genus mapped screening에서 area
> 700.074, setup slack +0.224 ns, vectorless power 0.127932 mW를 보였다.
> 별도의 polarity-extended top은 3.5 ns Innovus post-route에서 area
> 1254.114, setup/hold +0.454/+0.167 ns, vectorless power 0.10738887 mW,
> internal DRC/antenna 0을 보였지만, 원본 top과 다른 unsealed server-local
> implementation이다.

말하면 안 되는 주장:

- 원본 pinned `cluster2_steal_buf`가 Innovus P&R까지 통과했다.
- polarity top 수치를 원본 top PPA로 부른다.
- 285.714 MHz, 333.333 MHz 또는 500 MHz를 확정 Fmax라고 부른다.
- vectorless power를 VCD/SAIF activity power나 workload energy/event로 부른다.
- internal DRC/antenna 0을 foundry signoff, LVS/connectivity, ERC, IR/EM,
  SI/OCV signoff로 확대한다.
- Innovus run이 error-free였다고 말한다.
- 이 수치를 CAV/world RTL PPA로 합친다.

## 4. 현수의 release 패키징 완료 조건

1. 원본과 polarity top을 별도 후보/결과로 유지한다.
2. 발표에 사용할 source, filelist, SDC/MMMC/TCL, reports와 log를 Git tracked
   또는 immutable bundle로 봉인한다.
3. 각 파일의 SHA-256, 생성 시각, tool/version, PDK library/corner를 manifest에
   기록한다.
4. top/RTL hash, clock/I/O/load/uncertainty, activity mode를 표에 함께 표시한다.
5. setup/hold/check_timing/DRC/antenna와 `write_db` 오류를 누락하지 않는다.
6. fresh extraction 후 report parser가 동일 표를 재생하는지 확인한다.
7. 위 조건 전까지 evidence matrix의 native PPA 판정은 HOLD다.
