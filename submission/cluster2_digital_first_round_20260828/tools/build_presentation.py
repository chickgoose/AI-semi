#!/usr/bin/env python3
"""Build the Cluster2 presentation from a clean, projection-first layout system."""

from pathlib import Path
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx"

PAPER = RGBColor(247, 249, 252)
WHITE = RGBColor(255, 255, 255)
INK = RGBColor(15, 34, 52)
NAVY = RGBColor(19, 52, 79)
SLATE = RGBColor(76, 96, 115)
LINE = RGBColor(218, 226, 234)
PALE = RGBColor(235, 241, 246)
TEAL, TEAL_P = RGBColor(0, 158, 150), RGBColor(224, 246, 243)
BLUE, BLUE_P = RGBColor(37, 99, 235), RGBColor(232, 239, 255)
ORANGE, ORANGE_P = RGBColor(230, 132, 20), RGBColor(255, 242, 220)
RED, RED_P = RGBColor(210, 58, 70), RGBColor(254, 233, 235)
GREEN, GREEN_P = RGBColor(31, 147, 94), RGBColor(228, 247, 237)
PURPLE = RGBColor(124, 74, 184)
FONT = "Malgun Gothic"


def box(slide, x, y, w, h, fill=WHITE, stroke=LINE, rounded=True, sw=1.0):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    obj = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    obj.fill.solid(); obj.fill.fore_color.rgb = fill
    if stroke is None:
        obj.line.fill.background()
    else:
        obj.line.color.rgb = stroke; obj.line.width = Pt(sw)
    return obj


def text(slide, value, x, y, w, h, size=18, color=INK, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    obj = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = obj.text_frame; tf.clear(); tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE; tf.vertical_anchor = valign
    tf.margin_left = tf.margin_right = Inches(0.03)
    tf.margin_top = tf.margin_bottom = Inches(0.015)
    p = tf.paragraphs[0]; p.text = value; p.alignment = align
    p.font.name = FONT; p.font.size = Pt(size); p.font.bold = bold
    p.font.color.rgb = color; p.space_after = Pt(0)
    return obj


def rule(slide, x1, y1, x2, y2, color=LINE, width=1.5):
    obj = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    obj.line.color.rgb = color; obj.line.width = Pt(width)
    return obj


def arrow(slide, x, y, w=0.34, h=0.24, color=SLATE):
    obj = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    obj.fill.solid(); obj.fill.fore_color.rgb = color; obj.line.fill.background()
    return obj


def pill(slide, value, x, y, w, fill, color=WHITE, size=11):
    size = max(size, 12)
    box(slide, x, y, w, 0.34, fill, None)
    text(slide, value, x + 0.06, y + 0.075, w - 0.12, 0.18, size, color, True, PP_ALIGN.CENTER)


def header(prs, section, title, number, source):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = PAPER
    pill(slide, section, 0.62, 0.34, 1.25, NAVY)
    text(slide, title, 2.10, 0.20, 10.55, 0.62, 30, INK, True)
    rule(slide, 0.62, 0.96, 12.68, 0.96)
    text(slide, source, 0.64, 7.11, 11.35, 0.20, 9.5, SLATE)
    text(slide, f"{number:02d}", 12.04, 7.10, 0.60, 0.20, 12, NAVY, True, PP_ALIGN.RIGHT)
    return slide


def takeaway(slide, value, accent=TEAL, pale=TEAL_P):
    box(slide, 0.72, 6.28, 11.90, 0.62, pale, None)
    box(slide, 0.72, 6.28, 0.10, 0.62, accent, None, False)
    text(slide, value, 0.98, 6.43, 11.38, 0.28, 17, accent, True, PP_ALIGN.CENTER)


def card_head(slide, tag, title, x, y, w, accent):
    text(slide, tag, x + 0.22, y + 0.20, w - 0.44, 0.20, 12, accent, True)
    text(slide, title, x + 0.22, y + 0.50, w - 0.44, 0.34, 20, INK, True)


def stage(slide, x, y, w, tag, body, accent, pale, note=None):
    box(slide, x, y, w, 1.36 if note is None else 2.32, WHITE, accent, True, 1.5)
    text(slide, tag, x + 0.16, y + 0.22, w - 0.32, 0.18, 12, accent, True, PP_ALIGN.CENTER)
    text(slide, body, x + 0.16, y + 0.58, w - 0.32, 0.48, 16, INK, True, PP_ALIGN.CENTER)
    if note:
        box(slide, x + 0.22, y + 1.48, w - 0.44, 0.52, pale, None)
        text(slide, note, x + 0.30, y + 1.64, w - 0.60, 0.22, 12, accent, True, PP_ALIGN.CENTER)


def build():
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)

    # 01. Cover: architecture and the three proof anchors.
    s = prs.slides.add_slide(prs.slide_layouts[6]); s.background.fill.solid(); s.background.fill.fore_color.rgb = NAVY
    pill(s, "DIGITAL 1차 결과", 0.72, 0.52, 1.72, TEAL)
    text(s, "Cluster2 Steal-Buffer\nPolarity AER", 0.72, 1.15, 7.20, 1.42, 34, WHITE, True)
    text(s, "동시성은 넓히고 · 재발생은 흡수하고 · event 의미는 보존한다", 0.76, 2.75, 8.20, 0.42, 20, RGBColor(210, 226, 238), True)
    items = [("16 SOURCES", "event + polarity", BLUE), ("LOCAL", "depth-2", ORANGE),
             ("SCHEDULE", "2 lanes + steal", TEAL), ("RETIRE", "row / mask / pol", GREEN)]
    for i, (tag, body, accent) in enumerate(items):
        x = 0.76 + i * 2.95
        box(s, x, 3.58, 2.54, 1.12, WHITE, None)
        text(s, tag, x + 0.14, 3.80, 2.26, 0.18, 12, accent, True, PP_ALIGN.CENTER)
        text(s, body, x + 0.14, 4.14, 2.26, 0.24, 16, INK, True, PP_ALIGN.CENTER)
        if i < 3: arrow(s, x + 2.62, 4.02, 0.24, 0.20, RGBColor(141, 173, 196))
    proofs = [("FULL50 LOSS", "11.52% → 0.47%", "base 대비 −95.9%", TEAL),
              ("POLARITY REPLAY", "8,503 / 8,503", "mismatch 0", BLUE),
              ("FASTEST TESTED PASS", "285.714 MHz", "post-route", ORANGE)]
    for i, (tag, value, note, accent) in enumerate(proofs):
        x = 0.76 + i * 4.08
        box(s, x, 5.25, 3.74, 1.30, RGBColor(28, 69, 98), None)
        text(s, tag, x + 0.22, 5.47, 3.30, 0.18, 12, accent, True)
        text(s, value, x + 0.22, 5.80, 3.30, 0.30, 20, WHITE, True)
        text(s, note, x + 0.22, 6.22, 3.30, 0.18, 12, RGBColor(196, 216, 231))
    text(s, "AI-semi · 2026-08-28", 0.76, 7.04, 4.0, 0.18, 12, RGBColor(180, 202, 219))

    # 02. Six requirements.
    s = header(prs, "01 문제", "Ryu의 문제 흐름을 여섯 설계 요구로 분해했다", 2,
               "Framing: team decomposition of Ryu's problem flow; not a literal numbered list")
    limits = [("01", "주소 overhead", "bitmap + codec 후보", "PARTIAL", ORANGE, ORANGE_P),
              ("02", "직렬 bandwidth", "2 row-bitmap lanes", "DIRECT", TEAL, TEAL_P),
              ("03", "arbitration latency", "grouped retire", "DIRECT", TEAL, TEAL_P),
              ("04", "class 불균형", "rotate + lane steal", "STRUCTURAL", BLUE, BLUE_P),
              ("05", "timestamp mismatch", "payload 미포함", "HOLD", RED, RED_P),
              ("06", "motion artifact", "sensor/system 영역", "HOLD", RED, RED_P)]
    for i, (num, title, answer, status, accent, pale) in enumerate(limits):
        x, y = 0.72 + (i % 3) * 4.02, 1.30 + (i // 3) * 2.18
        box(s, x, y, 3.72, 1.82)
        pill(s, num, x + 0.20, y + 0.18, 0.52, accent)
        pill(s, status, x + 2.26, y + 0.18, 1.24, pale, accent, 10)
        text(s, title, x + 0.22, y + 0.72, 3.24, 0.30, 18, INK, True)
        text(s, answer, x + 0.22, y + 1.18, 3.24, 0.30, 15, SLATE)
    takeaway(s, "1차 직접 목표: ②·③·④ 완화 + polarity integrity 추가")

    # 03. Causal chain and measured baselines.
    s = header(prs, "01 문제", "직렬 출력과 source 재발생이 loss를 만든다", 3,
               "Source: common full50 recovered diagnostics · 106,416 offered events/design")
    chain = [("동시 입력", "16 sources", BLUE), ("공유 선택", "1 event/cycle", ORANGE),
             ("대기 누적", "pending", RED), ("같은 source 재발생", "local full", RED),
             ("SOURCE OVERRUN", "loss", RED)]
    for i, (a, b, accent) in enumerate(chain):
        x = 0.70 + i * 2.48
        box(s, x, 1.42, 2.14, 1.24, WHITE, accent, True, 1.5)
        text(s, a, x + 0.10, 1.72, 1.94, 0.26, 15, INK, True, PP_ALIGN.CENTER)
        text(s, b, x + 0.10, 2.12, 1.94, 0.20, 12, accent, True, PP_ALIGN.CENTER)
        if i < 4: arrow(s, x + 2.18, 1.90, 0.24, 0.22)
    for x, tag, title, pct, count, note, accent in [
        (0.72, "SCALAR FOVEA", "한 cycle 한 event", "26.49%", "28,187 source-overrun", "delivered 78,229 · EPC 0.673901", RED),
        (6.78, "BASE CLUSTER2", "병렬화만으로 재발생은 남는다", "11.52%", "12,259 source-overrun", "다음 해법: local depth + idle capacity reuse", ORANGE)]:
        box(s, x, 3.15, 5.84, 2.52); card_head(s, tag, title, x, 3.15, 5.84, accent)
        text(s, pct, x + 0.32, 4.14, 2.70, 0.62, 36, accent, True)
        text(s, count, x + 0.34, 4.86, 4.90, 0.30, 17, INK, True)
        text(s, note, x + 0.34, 5.27, 4.90, 0.24, 13, SLATE)
    takeaway(s, "출력 폭 · source-local 흡수 · class capacity를 함께 풀어야 한다", NAVY, PALE)

    # 04. Complete architecture.
    s = header(prs, "02 구조", "최종 구조는 네 단계로 읽힌다", 4,
               "Source: final top aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity")
    stages = [("1 · ARRIVAL", "16 event\n+ polarity", BLUE, BLUE_P, "occurrence"),
              ("2 · LOCAL", "source별\ndepth-2", ORANGE, ORANGE_P, "count + slots"),
              ("3 · SCHEDULE", "2 lanes\n+ steal", TEAL, TEAL_P, "rotate + reuse"),
              ("4 · RETIRE", "row + mask\n+ pol_mask", GREEN, GREEN_P, "selected events")]
    for i, args in enumerate(stages):
        x = 0.78 + i * 3.02; stage(s, x, 1.48, 2.66, *args)
        if i < 3: arrow(s, x + 2.72, 2.48, 0.24, 0.22)
    box(s, 1.14, 4.28, 11.05, 0.72, NAVY, None)
    text(s, "2 × { valid, row, col_mask[3:0], pol_mask[3:0] }  →  최대 8 events/cycle 표현",
         1.36, 4.51, 10.61, 0.28, 17, WHITE, True, PP_ALIGN.CENTER)
    text(s, "full address FIFO가 아니라 source index에 내재한 pending count와 polarity slot을 상태로 유지",
         1.10, 5.35, 11.14, 0.30, 15, SLATE, True, PP_ALIGN.CENTER)
    takeaway(s, "주소와 polarity는 동일 source slot에서 선택되고 함께 retire한다")

    # 05. Bitmap vs depth-2.
    s = header(prs, "02 구조", "bitmap은 직렬화를, depth-2는 재발생을 줄인다", 5,
               "Source: final RTL architecture contract")
    box(s, 0.72, 1.30, 5.80, 4.55); card_head(s, "IDEA 1", "ROW BITMAP", 0.72, 1.30, 5.80, BLUE)
    text(s, "row 2", 1.08, 2.30, 1.10, 0.24, 15, SLATE, True)
    for i in range(4):
        x = 2.22 + i * 0.76; box(s, x, 2.17, 0.56, 0.56, BLUE_P, BLUE)
        text(s, "1", x, 2.34, 0.56, 0.20, 16, BLUE, True, PP_ALIGN.CENTER)
    text(s, "네 scalar 주소", 1.08, 3.20, 1.62, 0.28, 16, INK, True); arrow(s, 2.90, 3.20, 0.52, 0.28, BLUE)
    box(s, 3.70, 3.03, 2.18, 0.70, BLUE_P, None); text(s, "row + 1111", 3.90, 3.26, 1.78, 0.24, 17, BLUE, True, PP_ALIGN.CENTER)
    text(s, "한 lane transaction = 최대 4 events", 1.08, 4.30, 4.95, 0.30, 16, SLATE)
    box(s, 6.82, 1.30, 5.80, 4.55); card_head(s, "IDEA 2", "SOURCE-LOCAL DEPTH-2", 6.82, 1.30, 5.80, ORANGE)
    text(s, "같은 source", 7.18, 2.30, 1.42, 0.24, 15, SLATE, True)
    for x, label in [(8.82, "slot 0\nA"), (10.34, "slot 1\nB")]:
        box(s, x, 2.08, 1.30, 0.82, ORANGE_P, ORANGE); text(s, label, x, 2.25, 1.30, 0.44, 15, ORANGE, True, PP_ALIGN.CENTER)
    text(s, "A가 grant를 기다리는 동안 B 재발생", 7.18, 3.35, 4.95, 0.30, 16, INK, True)
    text(s, "두 번째 occurrence를 overrun 대신 저장", 7.18, 4.28, 4.95, 0.30, 16, SLATE)
    takeaway(s, "bitmap은 serialization을, depth-2는 same-source recurrence를 줄인다", NAVY, PALE)

    # 06. Steal and polarity.
    s = header(prs, "02 구조", "steal은 capacity를, lockstep은 의미를 지킨다", 6,
               "Source: final polarity RTL · native observational contract")
    box(s, 0.72, 1.30, 5.80, 4.55); card_head(s, "IDEA 3", "CONDITIONAL LANE STEAL", 0.72, 1.30, 5.80, PURPLE)
    for x, title, body, accent, pale in [(1.10, "CENTER", "busy", TEAL, TEAL_P), (4.20, "PERIPH.", "idle", SLATE, PALE)]:
        box(s, x, 2.25, 1.76, 1.15, pale, accent); text(s, title, x, 2.48, 1.76, 0.22, 12, accent, True, PP_ALIGN.CENTER)
        text(s, body, x, 2.86, 1.76, 0.28, 18, accent, True, PP_ALIGN.CENTER)
    arrow(s, 3.66, 2.68, 0.36, 0.24, PURPLE)
    text(s, "idle lane capacity를 반대 class가 재사용", 1.08, 4.08, 5.10, 0.30, 16, PURPLE, True, PP_ALIGN.CENTER)
    text(s, "global fairness 수치는 별도", 1.08, 4.62, 5.10, 0.24, 13, SLATE, False, PP_ALIGN.CENTER)
    box(s, 6.82, 1.30, 5.80, 4.55); card_head(s, "IDEA 4", "POLARITY LOCKSTEP", 6.82, 1.30, 5.80, BLUE)
    for i, (name, addr, pol) in enumerate([("A", "row2 · col1", "pol 0"), ("B", "row2 · col2", "pol 1")]):
        y = 2.14 + i * 1.05; box(s, 7.22, y, 5.00, 0.78, BLUE_P, BLUE)
        text(s, name, 7.46, y + 0.24, 0.38, 0.22, 15, BLUE, True)
        text(s, addr, 8.06, y + 0.24, 2.08, 0.22, 15, INK, True)
        text(s, pol, 10.56, y + 0.24, 1.30, 0.22, 15, BLUE, True, PP_ALIGN.RIGHT)
    text(s, "address와 polarity를 같은 slot에서 push / pop", 7.20, 4.64, 5.04, 0.26, 15, BLUE, True, PP_ALIGN.CENTER)
    takeaway(s, "steal은 capacity 낭비를 줄이고, lockstep은 selected-event 의미를 보존한다")

    # 07. Encoding boundary.
    s = header(prs, "02 구조", "주소 절감은 제출 RTL과 후속 실험을 분리했다", 7,
               "Source: row-trim/repeat-flag address-only studies · final polarity boundary")
    cards = [(0.72, "SUBMIT", "ROW + MASK", "현재 최종 RTL", "pol_mask 포함", TEAL, TEAL_P),
             (4.76, "STUDY", "ROW-TRIM", "−14.29%", "base Cluster2 전용", SLATE, PALE),
             (8.80, "NEXT", "REPEAT-FLAG", "−15.61%", "polarity 재검증", ORANGE, ORANGE_P)]
    for x, badge, title, value, note, accent, pale in cards:
        box(s, x, 1.42, 3.64, 4.22, WHITE, accent, True, 1.5); pill(s, badge, x + 0.24, 1.72, 1.02, accent)
        text(s, title, x + 0.24, 2.35, 3.16, 0.34, 20, INK, True, PP_ALIGN.CENTER)
        text(s, value, x + 0.24, 3.10, 3.16, 0.56, 28, accent, True, PP_ALIGN.CENTER)
        box(s, x + 0.32, 4.15, 3.00, 0.76, pale, None); text(s, note, x + 0.46, 4.39, 2.72, 0.28, 14, accent, True, PP_ALIGN.CENTER)
    takeaway(s, "2차 codec 후보는 repeat-flag — polarity 포함 end-to-end 검증 후 통합", ORANGE, ORANGE_P)

    # 08. Hero result.
    s = header(prs, "03 결과", "full50 source-overrun: 12,259 → 502", 8,
               "Source: same 50 traces · 106,416 offered events/design · family diagnostic")
    box(s, 0.72, 1.22, 8.15, 4.66)
    data = [("SCALAR\nFOVEA", 26.49, "28,187", RED), ("BASE\nCLUSTER2", 11.52, "12,259", ORANGE),
            ("CLUSTER2\nSTEAL_BUF", 0.47, "502", TEAL)]
    for i, (label, pct, count, accent) in enumerate(data):
        x = 1.30 + i * 2.45; h = max(0.12, 2.35 * pct / 26.49)
        box(s, x + 0.42, 4.53 - h, 1.18, h, accent, None, False)
        text(s, f"{pct:.2f}%", x, 1.52 if i == 2 else 4.05 - h, 2.02, 0.40, 24, accent, True, PP_ALIGN.CENTER)
        text(s, label, x, 4.78, 2.02, 0.58, 13, INK, True, PP_ALIGN.CENTER)
        text(s, f"loss {count}", x, 5.45, 2.02, 0.22, 12, SLATE, False, PP_ALIGN.CENTER)
    box(s, 9.16, 1.22, 3.46, 4.66, NAVY, None)
    text(s, "BASE → STEAL_BUF", 9.48, 1.65, 2.82, 0.22, 12, RGBColor(185, 213, 230), True, PP_ALIGN.CENTER)
    text(s, "−95.9%", 9.42, 2.27, 2.94, 0.66, 35, WHITE, True, PP_ALIGN.CENTER)
    text(s, "relative loss reduction", 9.48, 3.02, 2.82, 0.24, 13, RGBColor(185, 213, 230), False, PP_ALIGN.CENTER)
    rule(s, 9.62, 3.62, 12.14, 3.62, RGBColor(77, 111, 137))
    text(s, "24.42×", 9.48, 3.92, 2.82, 0.44, 25, TEAL, True, PP_ALIGN.CENTER)
    text(s, "smaller remaining loss", 9.48, 4.42, 2.82, 0.22, 13, RGBColor(185, 213, 230), False, PP_ALIGN.CENTER)
    text(s, "48 / 50 traces: overrun 0", 9.42, 5.14, 2.94, 0.24, 13, WHITE, True, PP_ALIGN.CENTER)
    takeaway(s, "steal-buffer의 핵심 효과: 기본 Cluster2의 남은 재발생 손실을 직접 줄였다")

    # 09. Simulator and actual cycle.
    s = header(prs, "03 결과", "cycle 4,162: 두 lane이 3 events를 retire했다", 9,
               "Source: Xcelium 23.09-s013 · redred_cluster2_polarity_v1_native_observational_tb.sv")
    flow = [("INPUT", "UZH 4×4 patch\n8,503 events", BLUE), ("SIM", "Xcelium\nRTL + TB", ORANGE),
            ("LEDGER", "raw cycle trace\nindependent Python", PURPLE), ("RESULT", "8,503 / 8,503\nPASS", TEAL)]
    for i, (tag, body, accent) in enumerate(flow):
        x = 0.72 + i * 3.02; stage(s, x, 1.30, 2.64, tag, body, accent, PALE)
        if i < 3: arrow(s, x + 2.70, 1.80, 0.22, 0.20)
    box(s, 0.72, 2.96, 7.56, 2.98); card_head(s, "ACTUAL RETIRE · CYCLE 4,162", "두 lane에서 합계 3 events", 0.72, 2.96, 7.56, NAVY)
    rows = [("LANE 0", "row 2", "col 0x6", "pol 0x0", "2 events", BLUE),
            ("LANE 1", "row 0", "col 0x1", "pol 0x1", "1 event", TEAL)]
    for i, (lane, row, col, pol, n, accent) in enumerate(rows):
        y = 4.10 + i * 0.72; pill(s, lane, 1.02, y, 1.02, accent, WHITE, 10)
        text(s, row, 2.28, y + 0.08, 0.90, 0.22, 14, INK, True); text(s, col, 3.35, y + 0.08, 1.52, 0.22, 14, INK, True)
        text(s, pol, 5.02, y + 0.08, 1.52, 0.22, 14, INK, True); text(s, n, 6.73, y + 0.08, 1.18, 0.22, 14, accent, True, PP_ALIGN.RIGHT)
    box(s, 8.60, 2.96, 4.02, 2.98, NAVY, None)
    text(s, "CONSERVATION", 8.94, 3.34, 3.34, 0.20, 12, RGBColor(188, 214, 231), True, PP_ALIGN.CENTER)
    text(s, "8,503 = 8,503 + 0", 8.92, 3.88, 3.38, 0.38, 22, WHITE, True, PP_ALIGN.CENTER)
    text(s, "generated   delivered   overrun", 8.92, 4.32, 3.38, 0.22, 12, RGBColor(188, 214, 231), False, PP_ALIGN.CENTER)
    text(s, "phantom 0 · duplicate 0\npolarity mismatch 0 · drain empty", 8.94, 4.88, 3.34, 0.58, 14, TEAL, True, PP_ALIGN.CENTER)
    takeaway(s, "TB의 PASS 문자열이 아니라 raw ledger를 독립 경로로 재해석했다", NAVY, PALE)

    # 10. Physical implementation.
    s = header(prs, "04 구현", "최종 top: 285.714 MHz post-route PASS", 12,
               "Source: Genus 23.14-s090_1 · Innovus 23.14-s088_1 · GPDK045 slow 0.9 V 125 °C")
    box(s, 0.72, 1.26, 7.65, 3.18); card_head(s, "POST-ROUTE TIMING", "fastest tested passing point", 0.72, 1.26, 7.65, TEAL)
    rule(s, 1.20, 3.42, 7.86, 3.42, SLATE, 2.0)
    for x, freq, status, accent in [(1.30, "200 MHz", "PASS", GREEN), (3.35, "250 MHz", "PASS", GREEN),
                                    (5.42, "285.714 MHz", "PASS", TEAL), (7.46, "333.333 MHz", "FAIL", RED)]:
        box(s, x, 3.22, 0.16, 0.40, accent, None, False)
        text(s, freq, x - 0.55, 2.55, 1.26, 0.24, 13, INK, True, PP_ALIGN.CENTER)
        text(s, status, x - 0.45, 2.93, 1.06, 0.20, 12, accent, True, PP_ALIGN.CENTER)
    text(s, "285.714 MHz: setup +0.454 ns · hold +0.167 ns", 1.14, 3.77, 6.86, 0.28, 15, TEAL, True, PP_ALIGN.CENTER)
    for y, tag, value, note, accent in [(1.26, "AREA", "1254.114", "596 instances · raw", ORANGE),
                                        (2.97, "POWER", "0.1074 mW", "vectorless · activity 0.2", PURPLE)]:
        box(s, 8.68, y, 3.94, 1.47); text(s, tag, 8.96, y + 0.29, 1.02, 0.20, 12, accent, True)
        text(s, value, 8.96, y + 0.67, 2.54, 0.36, 22, INK, True); text(s, note, 8.96, y + 1.08, 3.00, 0.20, 12, SLATE)
    box(s, 0.72, 4.76, 11.90, 1.10, PALE, None); text(s, "EVIDENCE BOUNDARY", 1.00, 4.98, 1.72, 0.20, 12, NAVY, True)
    text(s, "fastest tested PASS ≠ exact Fmax · area unit 미기재 · vectorless power ≠ workload signoff power",
         2.84, 4.95, 9.46, 0.42, 14, SLATE, True, PP_ALIGN.CENTER)
    text(s, "internal DRC 0 · antenna 0", 2.84, 5.45, 9.46, 0.22, 13, GREEN, True, PP_ALIGN.CENTER)
    takeaway(s, "1차 제출 범위: 동작 RTL + tested timing/area/power — signoff 과장은 하지 않는다", NAVY, PALE)

    # 11. CAV fork.
    s = header(prs, "05 확장", "CAV 확장 경로: 8,503 events 전수 분기", 12,
               "Source: sealed legacy address-only CAV software path · separate from final polarity RTL/PPA")
    top = [(0.72, 3.05, "UZH EVENTS + POSE", "8,503 occurrences", BLUE),
           (4.65, 3.56, "IDENTITY JOIN", "8,503 / 8,503", PURPLE),
           (9.08, 3.54, "CAUSAL-CAV", "occurrence-time geometry", TEAL)]
    for i, (x, w, tag, body, accent) in enumerate(top):
        box(s, x, 1.26, w, 1.32, WHITE, accent, True, 1.5)
        text(s, tag, x + 0.20, 1.57, w - 0.40, 0.22, 12, accent, True, PP_ALIGN.CENTER)
        text(s, body, x + 0.20, 1.98, w - 0.40, 0.28, 16, INK, True, PP_ALIGN.CENTER)
        if i < 2: arrow(s, x + w + 0.20, 1.76, 0.44, 0.26)
    rule(s, 10.85, 2.60, 10.85, 3.18, TEAL, 2.0); rule(s, 7.30, 3.18, 11.62, 3.18, TEAL, 2.0)
    rule(s, 7.30, 3.18, 7.30, 3.48, TEAL, 2.0); rule(s, 11.62, 3.18, 11.62, 3.48, TEAL, 2.0)
    for x, w, tag, value, note, accent in [(0.72, 4.44, "현재 증거 범위", "software feasibility", "polarity→CAV RTL/PPA는 HOLD", RED),
                                           (5.64, 3.32, "WORLD", "8,420 events", "821 grid cells", GREEN),
                                           (9.92, 2.70, "SENSOR_FIXED", "83 bypass", "", ORANGE)]:
        box(s, x, 3.46, w, 1.54, RED_P if accent == RED else WHITE, accent if accent != RED else None, True, 1.5)
        text(s, tag, x + 0.28, 3.78, w - 0.56, 0.22, 12, accent, True, PP_ALIGN.CENTER)
        text(s, value, x + 0.28, 4.17, w - 0.56, 0.26, 17, INK, True, PP_ALIGN.CENTER)
        if note: text(s, note, x + 0.28, 4.56, w - 0.56, 0.22, 13, accent if accent == RED else SLATE, True, PP_ALIGN.CENTER)
    takeaway(s, "확장 경로는 확인했지만 final polarity proof chain과 섞지 않았다", PURPLE, PALE)

    # 12. Proven outcomes and next gates.
    s = header(prs, "06 결론", "1차 성과 세 가지와 2차 검증 세 가지", 12,
               "Evidence: full50 family diagnostic · final UZH polarity replay · tested physical flow")
    proofs = [("LOSS", "−95.9%", "base Cluster2 → steal_buf", TEAL),
              ("MEANING", "8,503 / 8,503", "polarity mismatch 0", BLUE),
              ("IMPLEMENT", "285.714 MHz", "fastest tested PASS", ORANGE)]
    for i, (tag, value, note, accent) in enumerate(proofs):
        x = 0.72 + i * 4.02; box(s, x, 1.28, 3.72, 2.05, WHITE, accent, True, 1.5)
        pill(s, tag, x + 0.24, 1.54, 1.36, accent)
        text(s, value, x + 0.24, 2.08, 3.24, 0.44, 25, accent, True, PP_ALIGN.CENTER)
        text(s, note, x + 0.24, 2.72, 3.24, 0.28, 13, SLATE, True, PP_ALIGN.CENTER)
    text(s, "2차 과제", 0.76, 3.78, 1.44, 0.30, 18, NAVY, True)
    roadmap = [("01", "LINK", "repeat-flag + polarity grammar", ORANGE),
               ("02", "CAV", "source timestamp + full replay", PURPLE),
               ("03", "PPA", "activity power + exact Fmax", TEAL)]
    for i, (num, tag, body, accent) in enumerate(roadmap):
        x = 0.72 + i * 4.02; box(s, x, 4.22, 3.72, 1.38, PALE, None)
        pill(s, num, x + 0.20, 4.48, 0.52, accent)
        text(s, tag, x + 0.88, 4.48, 0.78, 0.22, 13, accent, True)
        text(s, body, x + 0.88, 4.87, 2.56, 0.42, 15, INK, True)
    takeaway(s, "Cluster2 위에 depth-2·steal·polarity를 통합해 손실과 의미를 함께 다뤘다", NAVY, PALE)

    prs.core_properties.title = "Cluster2 Steal-Buffer Polarity AER"
    prs.core_properties.subject = "Ground-up bottleneck, architecture, evidence, physical implementation, and CAV extension"
    prs.core_properties.author = "AI-semi team"
    OUT.parent.mkdir(parents=True, exist_ok=True); prs.save(OUT)
    print(f"WROTE {OUT} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    build()
