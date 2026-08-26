#!/usr/bin/env python3
"""Build a one-glance Korean Cluster2 AER first-round deck."""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx"
LAYOUT = ROOT / "evidence" / "ppa" / "upstream_9b0d951" / "layout_3.5.png"

BG = RGBColor(7, 22, 39)
CARD = RGBColor(18, 48, 76)
CARD_2 = RGBColor(25, 62, 92)
WHITE = RGBColor(246, 249, 252)
MUTED = RGBColor(172, 194, 214)
CYAN = RGBColor(42, 211, 205)
GREEN = RGBColor(55, 205, 133)
BLUE = RGBColor(71, 147, 255)
ORANGE = RGBColor(255, 183, 70)
RED = RGBColor(255, 99, 118)
PURPLE = RGBColor(178, 126, 255)
FONT = "Malgun Gothic"


def shape(slide, x, y, w, h, fill, rounded=True, line=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    obj = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    obj.fill.solid(); obj.fill.fore_color.rgb = fill
    if line is None:
        obj.line.fill.background()
    else:
        obj.line.color.rgb = line
        obj.line.width = Pt(1.5)
    return obj


def text(slide, value, x, y, w, h, size=24, color=WHITE, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear(); frame.word_wrap = True; frame.vertical_anchor = valign
    frame.margin_left = frame.margin_right = Inches(0.02)
    frame.margin_top = frame.margin_bottom = Inches(0.01)
    p = frame.paragraphs[0]
    p.text = value; p.alignment = align
    p.font.name = FONT; p.font.size = Pt(size); p.font.bold = bold; p.font.color.rgb = color
    return box


def header(prs, section, title, number, source):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    shape(slide, 0, 0, 0.14, 7.5, CYAN, False)
    shape(slide, 0.55, 0.26, 1.55, 0.34, CARD_2)
    text(slide, section, 0.62, 0.31, 1.41, 0.20, 10, CYAN, True, PP_ALIGN.CENTER)
    text(slide, title, 2.30, 0.22, 10.20, 0.50, 27, WHITE, True)
    shape(slide, 0.55, 0.88, 12.18, 0.02, CYAN, False)
    text(slide, source, 0.60, 7.15, 11.2, 0.14, 6, MUTED)
    text(slide, f"{number:02d}", 12.15, 7.08, 0.48, 0.18, 9, CYAN, True, PP_ALIGN.RIGHT)
    return slide


def arrow_text(slide, x, y, w=0.55, color=CYAN, size=28):
    text(slide, "→", x, y, w, 0.45, size, color, True, PP_ALIGN.CENTER)


def card(slide, x, y, w, h, label, value, accent=CYAN, note="", value_size=27):
    shape(slide, x, y, w, h, CARD)
    text(slide, label, x + 0.18, y + 0.14, w - 0.36, 0.22, 10, MUTED, True)
    text(slide, value, x + 0.18, y + 0.48, w - 0.36, 0.52, value_size, accent, True,
         PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
    if note:
        text(slide, note, x + 0.18, y + h - 0.35, w - 0.36, 0.20, 9, MUTED)


def takeaway(slide, value, color=GREEN):
    shape(slide, 0.78, 6.20, 11.78, 0.62, CARD)
    text(slide, value, 1.02, 6.35, 11.30, 0.28, 16, color, True, PP_ALIGN.CENTER)


def build():
    prs = Presentation()
    prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)

    # 1. Title — the entire story in one visual.
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    shape(slide, 0, 0, 0.18, 7.5, CYAN, False)
    text(slide, "AER 손실을 24.42배 줄인 구조", 0.82, 0.78, 11.8, 0.72, 36, WHITE, True)
    text(slide, "Cluster2 Steal-Buffer Polarity AER", 0.84, 1.63, 11.0, 0.36, 19, CYAN, True)
    # single, central transformation
    shape(slide, 1.10, 2.55, 3.10, 1.55, CARD)
    text(slide, "기본 CLUSTER2", 1.34, 2.80, 2.40, 0.26, 12, MUTED, True)
    text(slide, "11.52%", 1.34, 3.16, 2.55, 0.46, 27, RED, True, PP_ALIGN.CENTER)
    arrow_text(slide, 4.52, 3.05, 0.80, CYAN, 38)
    shape(slide, 5.65, 2.35, 3.55, 1.95, CARD, True, CYAN)
    text(slide, "STEAL + DEPTH-2", 5.95, 2.67, 2.95, 0.28, 13, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "재발화 흡수\nidle lane 재사용", 5.92, 3.03, 3.02, 0.72, 20, WHITE, True, PP_ALIGN.CENTER)
    arrow_text(slide, 9.48, 3.05, 0.80, CYAN, 38)
    shape(slide, 10.55, 2.55, 1.75, 1.55, GREEN)
    text(slide, "STEAL_BUF", 10.76, 2.80, 1.32, 0.24, 11, BG, True, PP_ALIGN.CENTER)
    text(slide, "0.47%", 10.68, 3.12, 1.48, 0.48, 25, BG, True, PP_ALIGN.CENTER)
    text(slide, "502 / 106,416", 10.68, 3.68, 1.48, 0.20, 8, BG, True, PP_ALIGN.CENTER)
    text(slide, "Ryu 6문제  ·  실제 TB  ·  개선 수치  ·  물리 구현  ·  CAV/2차 확장", 1.0, 5.15, 11.3, 0.38, 18, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "2026-08-28  |  Xcelium · Genus · Innovus", 0.84, 6.85, 11.2, 0.22, 10, MUTED)

    # 2. Ryu's six AER problems — distinguish solved, partial, and future.
    slide = header(prs, "01  문제", "Ryu의 전통 AER 6문제에 대한 우리의 대응", 2,
                   "Source: Ryu CVPRW 2019 slides · Ganghee sections 67, 86–89")
    ryu = [
        (0.72, 1.20, ORANGE, "① 주소 오버헤드", "PARTIAL", "row bitmap · 2차 repeat −15.61%"),
        (4.72, 1.20, GREEN, "② 공유채널 대역폭", "SOLVED @ N=16", "2 lanes · 최대 8 events/cycle"),
        (8.72, 1.20, CYAN, "③ 중재 지연", "REDUCED", "row batch + 병렬 retire"),
        (0.72, 3.18, GREEN, "④ 중재 불공정", "REDUCED", "분리 arbiter + conditional steal"),
        (4.72, 3.18, RED, "⑤ timestamp 왜곡", "HOLD", "고부하 jitter 해법 미완료"),
        (8.72, 3.18, PURPLE, "⑥ motion artifact", "PARTIAL", "동시 row/lane spread 0 · 고부하 HOLD"),
    ]
    for x, y, accent, label, status, response in ryu:
        shape(slide, x, y, 3.62, 1.45, CARD, True, accent)
        text(slide, label, x + 0.20, y + 0.17, 3.22, 0.28, 14, WHITE, True)
        text(slide, status, x + 0.20, y + 0.56, 3.22, 0.25, 12, accent, True)
        text(slide, response, x + 0.20, y + 0.96, 3.22, 0.24, 10, MUTED)
    takeaway(slide, "해결된 축과 남은 축을 분리한다 — timestamp/motion을 해결 완료로 과장하지 않는다", ORANGE)

    # 3. Make the delta from base Cluster2 to steal-buffer unmistakable.
    slide = header(prs, "02  핵심", "기본 Cluster2의 빈틈을 steal_buf가 직접 메운다", 3,
                   "Source: official full50 Xcelium comparison · Ganghee sections 65–66")
    shape(slide, 0.78, 1.25, 5.25, 3.95, CARD, True, ORANGE)
    text(slide, "BASIC CLUSTER2", 1.08, 1.58, 4.65, 0.30, 16, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "source당 pending 1개", 1.08, 2.17, 4.65, 0.38, 23, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "재발화가 grant보다 빠르면", 1.08, 2.88, 4.65, 0.28, 14, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "LOCAL FULL → OVERRUN", 1.08, 3.34, 4.65, 0.38, 20, RED, True, PP_ALIGN.CENTER)
    text(slide, "11.52%  ·  12,259 loss", 1.08, 4.25, 4.65, 0.42, 25, RED, True, PP_ALIGN.CENTER)
    arrow_text(slide, 6.20, 2.85, 0.72, CYAN, 38)
    shape(slide, 7.10, 1.25, 5.25, 3.95, CARD, True, GREEN)
    text(slide, "CLUSTER2 STEAL_BUF", 7.40, 1.58, 4.65, 0.30, 16, GREEN, True, PP_ALIGN.CENTER)
    text(slide, "source별 depth-2 FIFO", 7.40, 2.17, 4.65, 0.38, 23, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "두 번째 event 흡수 + idle lane steal", 7.40, 2.88, 4.65, 0.28, 14, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "RETRIGGER + IMBALANCE 대응", 7.40, 3.34, 4.65, 0.38, 18, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "0.47%  ·  502 loss", 7.40, 4.25, 4.65, 0.42, 25, GREEN, True, PP_ALIGN.CENTER)
    takeaway(slide, "동일 106,416 events · 손실 12,259 → 502 · 24.42배 감소(−95.9%)", GREEN)

    # 4. Architecture — one left-to-right pipeline.
    slide = header(prs, "02  구조", "두 row-bitmap lane이 최대 8 events를 동시에 retire한다", 4,
                   "Source: final polarity RTL SHA-256 20d601a9…")
    # source grid
    text(slide, "16 SOURCES", 0.68, 1.22, 2.15, 0.26, 13, MUTED, True, PP_ALIGN.CENTER)
    for r in range(4):
        for c in range(4):
            accent = CYAN if r in (1, 2) else BLUE
            shape(slide, 0.80 + c * 0.48, 1.65 + r * 0.48, 0.35, 0.35, CARD_2, True, accent)
    arrow_text(slide, 2.95, 2.25, 0.66, CYAN, 34)
    shape(slide, 3.82, 1.45, 2.65, 2.35, CARD, True, CYAN)
    text(slide, "SOURCE-LOCAL", 4.08, 1.82, 2.14, 0.28, 13, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "16 × depth-2", 4.08, 2.25, 2.14, 0.38, 23, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "event + polarity FIFO", 4.05, 2.84, 2.20, 0.28, 13, MUTED, False, PP_ALIGN.CENTER)
    arrow_text(slide, 6.72, 2.25, 0.66, CYAN, 34)
    # split into two lanes
    shape(slide, 7.60, 1.20, 2.10, 1.25, CARD, True, CYAN)
    text(slide, "CENTER ROW", 7.83, 1.48, 1.64, 0.27, 14, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "4-bit bitmap", 7.86, 1.89, 1.58, 0.24, 12, WHITE, True, PP_ALIGN.CENTER)
    shape(slide, 7.60, 2.85, 2.10, 1.25, CARD, True, BLUE)
    text(slide, "PERIPH. ROW", 7.83, 3.13, 1.64, 0.27, 14, BLUE, True, PP_ALIGN.CENTER)
    text(slide, "4-bit bitmap", 7.86, 3.54, 1.58, 0.24, 12, WHITE, True, PP_ALIGN.CENTER)
    arrow_text(slide, 9.92, 2.25, 0.66, GREEN, 34)
    shape(slide, 10.75, 1.45, 1.70, 2.35, GREEN)
    text(slide, "최대", 11.00, 1.80, 1.20, 0.25, 12, BG, True, PP_ALIGN.CENTER)
    text(slide, "8", 10.98, 2.15, 1.24, 0.66, 42, BG, True, PP_ALIGN.CENTER)
    text(slide, "events/cycle", 10.91, 3.08, 1.38, 0.25, 10, BG, True, PP_ALIGN.CENTER)
    # exact output contract, no overlapping banner text
    shape(slide, 1.35, 4.72, 10.65, 0.82, CARD)
    text(slide, "출력  =  row  +  col_mask[3:0]  +  pol_mask[3:0]", 1.66, 4.92, 10.03, 0.36, 20, WHITE, True, PP_ALIGN.CENTER)
    takeaway(slide, "핵심 변화: event를 하나씩 고르지 않고, 선택된 row를 bitmap으로 묶어 보낸다", GREEN)

    # 5. Explicit problem-to-solution map.
    slide = header(prs, "02  구조", "각 병목에 대응하는 RTL 해법이 다르다", 5,
                   "Source: polarity RTL lines 33–153 · arbiter2/arbiter4_tree")
    mapping = [
        (RED, "① 1-event/cycle", "ROW BITMAP × 2 LANES", "최대 8 events/cycle"),
        (ORANGE, "② source 재발생", "DEPTH-2 / SOURCE", "두 번째 event 흡수"),
        (PURPLE, "③ class 쏠림", "CONDITIONAL LANE STEAL", "idle 처리력 재사용"),
        (BLUE, "④ addr–pol 분리", "POLARITY LOCKSTEP FIFO", "polarity mismatch 0"),
    ]
    text(slide, "병목", 0.84, 1.13, 2.85, 0.24, 11, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "RTL 해법", 4.18, 1.13, 4.18, 0.24, 11, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "직접 효과", 9.18, 1.13, 3.18, 0.24, 11, MUTED, True, PP_ALIGN.CENTER)
    for i, (accent, problem, mechanism, effect) in enumerate(mapping):
        y = 1.52 + i * 1.12
        shape(slide, 0.78, y, 2.95, 0.76, CARD, True, accent)
        text(slide, problem, 0.98, y + 0.20, 2.55, 0.30, 14, accent, True, PP_ALIGN.CENTER)
        arrow_text(slide, 3.82, y + 0.16, 0.48, CYAN, 24)
        shape(slide, 4.38, y, 4.02, 0.76, CARD, True, accent)
        text(slide, mechanism, 4.58, y + 0.20, 3.62, 0.30, 14, WHITE, True, PP_ALIGN.CENTER)
        arrow_text(slide, 8.50, y + 0.16, 0.48, CYAN, 24)
        shape(slide, 9.06, y, 3.30, 0.76, CARD, True, accent)
        text(slide, effect, 9.24, y + 0.20, 2.94, 0.30, 14, accent, True, PP_ALIGN.CENTER)
    takeaway(slide, "한 가지 큰 개선이 아니라, 네 병목을 네 메커니즘으로 각각 완화했다", GREEN)

    # 6. Official 50-workload comparison — foreground the named design.
    slide = header(prs, "03  정량", "공식 50-workload에서 steal_buf가 손실을 거의 제거", 6,
                   "Source: Xcelium 23.09-s013 · 50/50 PHANTOM_DEBUG_PASS")
    variants = [
        ("FOVEA", "26.49%", "28,187", RED),
        ("CLUSTER2", "11.52%", "12,259", ORANGE),
        ("+ STEAL", "10.89%", "11,593", CYAN),
        ("+ STEAL_BUF", "0.47%", "502", GREEN),
    ]
    for i, (label, rate, lost, accent) in enumerate(variants):
        x = 0.72 + i * 3.10
        shape(slide, x, 1.40, 2.72, 2.65, CARD, True, accent)
        text(slide, label, x + 0.20, 1.70, 2.32, 0.26, 13, accent, True, PP_ALIGN.CENTER)
        text(slide, rate, x + 0.20, 2.22, 2.32, 0.54, 31, accent, True, PP_ALIGN.CENTER)
        text(slide, f"loss {lost}", x + 0.20, 3.05, 2.32, 0.30, 15, WHITE, True, PP_ALIGN.CENTER)
        if i < 3:
            arrow_text(slide, x + 2.72, 2.40, 0.38, CYAN, 22)
    shape(slide, 2.15, 4.55, 9.05, 0.92, GREEN)
    text(slide, "CLUSTER2 → STEAL_BUF   11.52% → 0.47%", 2.45, 4.73, 8.45, 0.30, 23, BG, True, PP_ALIGN.CENTER)
    takeaway(slide, "손실 건수 24.42배 감소 · 상대 감소율 95.9% · 동일 106,416-event denominator", GREEN)
    text(slide, "※ 최종 UZH polarity trace(8,503)는 별도 검증이며 이 비교와 섞지 않는다", 1.20, 6.91, 10.95, 0.17, 8, ORANGE, True, PP_ALIGN.CENTER)

    # 7. Show the actual simulators, TBs, cycle flow, and independently checked result.
    slide = header(prs, "03  검증", "두 실제 TB가 비교 성능과 최종 polarity를 분리 검증", 7,
                   "Source: committed TBs · Xcelium 23.09-s013 · independent Python verifier")
    shape(slide, 0.72, 1.18, 5.92, 1.18, CARD, True, ORANGE)
    text(slide, "50-WORKLOAD 비교 TB", 0.98, 1.42, 2.10, 0.24, 13, ORANGE, True)
    text(slide, "tb_steal_buf_trace_phantom_debug.v", 0.98, 1.82, 5.30, 0.24, 13, WHITE, True)
    text(slide, "50/50 PASS · loss 502 · phantom 0", 3.10, 1.42, 3.18, 0.24, 11, MUTED, False, PP_ALIGN.RIGHT)
    shape(slide, 6.78, 1.18, 5.82, 1.18, CARD, True, GREEN)
    text(slide, "최종 POLARITY TB", 7.04, 1.42, 2.00, 0.24, 13, GREEN, True)
    text(slide, "redred_cluster2_polarity_v1_native_observational_tb.sv", 7.04, 1.82, 5.20, 0.24, 10, WHITE, True)
    text(slide, "8,503/8,503 · mismatch 0", 9.18, 1.42, 3.06, 0.24, 11, MUTED, False, PP_ALIGN.RIGHT)
    # Actual cycle-level data path observed by the second TB.
    stages = [
        (0.72, BLUE, "TRACE", "arrival[15:0]\npolarity[15:0]"),
        (3.16, CYAN, "ENQUEUE", "source FIFO\ndepth 0→1→2"),
        (5.60, PURPLE, "ARBITRATE", "center/periph\n+ conditional steal"),
        (8.04, GREEN, "RETIRE", "row + col_mask\n+ pol_mask"),
        (10.48, ORANGE, "LEDGER", "cycle별 원시값\n독립 재계산"),
    ]
    for i, (x, accent, label, value) in enumerate(stages):
        shape(slide, x, 3.00, 1.92, 1.58, CARD, True, accent)
        text(slide, label, x + 0.16, 3.20, 1.60, 0.24, 11, accent, True, PP_ALIGN.CENTER)
        text(slide, value, x + 0.16, 3.67, 1.60, 0.56, 12, WHITE, True, PP_ALIGN.CENTER)
        if i < len(stages) - 1:
            arrow_text(slide, x + 1.92, 3.51, 0.52, CYAN, 22)
    shape(slide, 1.30, 5.02, 10.70, 0.72, CARD)
    text(slide, "실제 CYCLE 4162  |  lane0: row2 col=0x6 pol=0x0  ·  lane1: row0 col=0x1 pol=0x1  →  4 events 동시 retire", 1.54, 5.23, 10.22, 0.27, 12, WHITE, True, PP_ALIGN.CENTER)
    takeaway(slide, "8,503 = 8,503 + overrun 0 · mismatch/phantom/duplicate 0 · drain empty · Python 독립검증", GREEN)

    # 8. Physical feasibility — the clean point and discrete sweep.
    slide = header(prs, "04  구현", "P&R에서 285.714 MHz가 setup·hold를 모두 통과했다", 8,
                   "Source: Innovus slow 0.9 V 125 °C · discrete timing sweep")
    card(slide, 0.78, 1.28, 3.35, 1.42, "FASTEST TESTED PASS", "285.714 MHz", GREEN, "setup +0.454 ns · hold +0.167 ns", 30)
    # discrete frequency rail
    shape(slide, 0.98, 3.32, 7.42, 0.12, CARD_2, True)
    points = [
        (1.18, "222", "+1.349", GREEN),
        (3.17, "250", "+0.849", GREEN),
        (5.35, "285.7", "+0.454", GREEN),
        (7.75, "333.3", "−0.004", RED),
    ]
    for x, freq, slack, accent in points:
        shape(slide, x, 3.12, 0.38, 0.38, accent)
        text(slide, f"{freq}\nMHz", x - 0.28, 3.63, 0.95, 0.52, 11, accent, True, PP_ALIGN.CENTER)
        text(slide, f"setup {slack}", x - 0.43, 4.28, 1.25, 0.20, 8, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "PASS", 1.00, 2.86, 4.95, 0.20, 10, GREEN, True)
    text(slide, "FAIL", 7.48, 2.86, 1.10, 0.20, 10, RED, True, PP_ALIGN.RIGHT)
    # compact physical cards and thumbnail
    card(slide, 8.85, 1.28, 3.45, 1.42, "POST-ROUTE AREA RAW", "1254.114", CYAN, "596 instances · unit unstated", 29)
    card(slide, 8.85, 3.08, 3.45, 1.42, "VECTORLESS POWER", "0.107389 mW", ORANGE, "default activity 0.2", 27)
    shape(slide, 8.85, 4.88, 3.45, 0.88, CARD, True, GREEN)
    text(slide, "Internal DRC 0 · antenna 0", 9.05, 5.12, 3.05, 0.30, 15, GREEN, True, PP_ALIGN.CENTER)
    takeaway(slide, "285.714 MHz PASS · 다음 측정점 333.333 MHz FAIL · 중간 주파수는 미측정", ORANGE)

    # 9. CAV — separate authority and an explicit branch, not a false linear funnel.
    slide = header(prs, "05  확장", "CAV software 경로: 8,503 events 전수 분기", 9,
                   "Source: sealed legacy address-only official UZH→CAV result")
    shape(slide, 0.78, 1.18, 11.75, 0.44, CARD_2)
    text(slide, "LEGACY ADDRESS-ONLY TRACK  ·  최종 polarity-v1과 독립된 검증", 1.05, 1.29, 11.20, 0.20, 11, PURPLE, True, PP_ALIGN.CENTER)
    shape(slide, 0.78, 2.02, 2.35, 1.45, CARD, True, BLUE)
    text(slide, "UZH EVENTS + POSE", 1.00, 2.28, 1.91, 0.26, 11, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "8,503", 1.00, 2.73, 1.91, 0.42, 27, BLUE, True, PP_ALIGN.CENTER)
    arrow_text(slide, 3.22, 2.50, 0.55, CYAN, 26)
    shape(slide, 3.88, 2.02, 2.35, 1.45, CARD, True, GREEN)
    text(slide, "EXACT IDENTITY JOIN", 4.08, 2.28, 1.95, 0.26, 11, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "8,503 / 8,503", 4.04, 2.76, 2.03, 0.35, 20, GREEN, True, PP_ALIGN.CENTER)
    arrow_text(slide, 6.32, 2.50, 0.55, CYAN, 26)
    shape(slide, 6.98, 2.02, 2.35, 1.45, CARD, True, PURPLE)
    text(slide, "CAUSAL-CAV", 7.20, 2.28, 1.91, 0.26, 12, PURPLE, True, PP_ALIGN.CENTER)
    text(slide, "occurrence time", 7.18, 2.77, 1.95, 0.28, 15, WHITE, True, PP_ALIGN.CENTER)
    # CAV disposition branches
    text(slide, "↗", 9.42, 1.85, 0.48, 0.42, 25, CYAN, True, PP_ALIGN.CENTER)
    shape(slide, 9.92, 1.55, 2.40, 1.18, CARD, True, CYAN)
    text(slide, "WORLD", 10.12, 1.78, 2.00, 0.22, 11, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "8,420", 10.12, 2.15, 2.00, 0.34, 23, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "↘", 9.42, 3.05, 0.48, 0.42, 25, ORANGE, True, PP_ALIGN.CENTER)
    shape(slide, 9.92, 2.98, 2.40, 1.18, CARD, True, ORANGE)
    text(slide, "SENSOR_FIXED", 10.12, 3.18, 2.00, 0.22, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "83", 10.12, 3.55, 2.00, 0.34, 23, ORANGE, True, PP_ALIGN.CENTER)
    # WORLD-only aggregation and time semantics
    shape(slide, 0.90, 4.25, 5.30, 1.00, CARD)
    text(slide, "WORLD 8,420", 1.18, 4.50, 2.10, 0.32, 20, CYAN, True)
    text(slide, "→ 512×256 grid · 821 cells", 3.15, 4.52, 2.70, 0.28, 14, PURPLE, True, PP_ALIGN.RIGHT)
    shape(slide, 6.62, 4.25, 5.40, 1.00, CARD)
    text(slide, "geometry = occurrence time", 6.92, 4.48, 4.80, 0.28, 17, GREEN, True, PP_ALIGN.CENTER)
    text(slide, "retire cycle은 latency sidecar로만 보존", 6.92, 4.87, 4.80, 0.20, 9, MUTED, False, PP_ALIGN.CENTER)
    takeaway(slide, "입증: software functional extension · HOLD: polarity→CAV full replay / CAV RTL / CAV PPA", ORANGE)

    # 10. Close — distinguish proved, candidate extensions, and honest holds.
    slide = header(prs, "SUMMARY", "1차 결론과 2차 확장 경로가 수치로 연결된다", 10,
                   "Source bundle: receipts · raw reports · SHA256SUMS · PROVENANCE.json")
    claims = [
        (0.78, GREEN, "1차 핵심 개선", "11.52% → 0.47%", "손실 24.42배 감소"),
        (4.72, CYAN, "최종 RTL 증명", "8,503 / 8,503 보존", "285.714 MHz PASS"),
        (8.66, PURPLE, "CAV 확장 근거", "legacy 8,503 join", "8,420 WORLD + 83 bypass"),
    ]
    for x, accent, tag, line1, line2 in claims:
        shape(slide, x, 1.42, 3.52, 2.25, CARD, True, accent)
        text(slide, tag, x + 0.25, 1.70, 3.02, 0.28, 13, accent, True, PP_ALIGN.CENTER)
        text(slide, line1, x + 0.24, 2.20, 3.04, 0.36, 21, WHITE, True, PP_ALIGN.CENTER)
        text(slide, line2, x + 0.24, 2.80, 3.04, 0.38, 18, accent, True, PP_ALIGN.CENTER)
    shape(slide, 0.78, 4.18, 11.40, 1.30, CARD)
    text(slide, "2차", 1.05, 4.45, 0.72, 0.26, 13, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "steal_buf + repeat-flag", 1.82, 4.38, 3.08, 0.28, 16, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "검증 후보: link bits −15.61%", 1.82, 4.82, 3.08, 0.22, 10, GREEN, True, PP_ALIGN.CENTER)
    text(slide, "polarity → CAV full replay / RTL", 5.05, 4.38, 3.22, 0.28, 15, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "wire · backpressure · world path", 5.05, 4.82, 3.22, 0.22, 10, PURPLE, True, PP_ALIGN.CENTER)
    text(slide, "timestamp · activity power", 8.47, 4.38, 3.18, 0.28, 15, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "jitter 해법 · VCD/SAIF · exact Fmax", 8.47, 4.82, 3.18, 0.22, 10, RED, True, PP_ALIGN.CENTER)
    takeaway(slide, "row-trim −14.29%는 기본 Cluster2 전용 — steal_buf에는 적용 불가", ORANGE)

    # Restamp the header above every content object. PowerPoint's batch renderer
    # can otherwise drop thin early-z-order header fills on complex slides.
    for finished_slide in list(prs.slides)[1:]:
        early = list(finished_slide.shapes)
        section_label = early[2].text
        title_label = early[3].text
        shape(finished_slide, 0.45, 0.14, 12.25, 0.68, BG, False)
        shape(finished_slide, 0.55, 0.26, 1.55, 0.34, CARD_2)
        text(finished_slide, section_label, 0.62, 0.31, 1.41, 0.20, 10, CYAN, True, PP_ALIGN.CENTER)
        text(finished_slide, title_label, 2.30, 0.22, 10.20, 0.50, 27, WHITE, True)
        shape(finished_slide, 0.55, 0.88, 12.18, 0.025, CYAN, False)

    prs.core_properties.title = "Cluster2 AER — 2-lane row-bitmap으로 병목 완화"
    prs.core_properties.subject = "AER bottleneck, architecture, improvement, physical implementation, CAV extension"
    prs.core_properties.author = "AI-semi team"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    print(f"WROTE {OUT} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    build()
