#!/usr/bin/env python3
"""Build the evidence-scoped Korean Cluster2 AER first-round deck."""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx"
LAYOUT = ROOT / "evidence" / "ppa" / "upstream_9b0d951" / "layout_3.5.png"

NAVY = RGBColor(8, 22, 41)
NAVY_2 = RGBColor(17, 42, 68)
NAVY_3 = RGBColor(24, 58, 86)
CYAN = RGBColor(41, 210, 202)
BLUE = RGBColor(67, 145, 255)
WHITE = RGBColor(245, 248, 252)
MUTED = RGBColor(169, 190, 211)
GREEN = RGBColor(58, 202, 134)
RED = RGBColor(255, 102, 118)
ORANGE = RGBColor(255, 183, 73)
PURPLE = RGBColor(174, 124, 255)
FONT = "Malgun Gothic"


def rect(slide, x, y, w, h, color, radius=False, line=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line:
        shape.line.color.rgb = line
        shape.line.width = Pt(1.2)
    else:
        shape.line.fill.background()
    return shape


def textbox(slide, text, x, y, w, h, size=24, color=WHITE, bold=False,
            align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = valign
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    p = frame.paragraphs[0]
    p.text = text
    p.alignment = align
    p.font.name = FONT
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.color.rgb = color
    return box


def base_slide(prs, title, number, section, source):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = NAVY
    rect(slide, Inches(0), Inches(0), Inches(0.13), Inches(7.5), CYAN)
    rect(slide, Inches(0.55), Inches(0.27), Inches(1.48), Inches(0.34), NAVY_3, True)
    textbox(slide, section, Inches(0.63), Inches(0.31), Inches(1.32), Inches(0.22), 9, CYAN, True, PP_ALIGN.CENTER)
    textbox(slide, title, Inches(2.18), Inches(0.25), Inches(10.0), Inches(0.48), 25, WHITE, True)
    rect(slide, Inches(0.55), Inches(0.88), Inches(12.2), Inches(0.02), CYAN)
    textbox(slide, source, Inches(0.58), Inches(7.10), Inches(11.5), Inches(0.18), 7, MUTED)
    textbox(slide, f"{number:02d}", Inches(12.10), Inches(7.00), Inches(0.55), Inches(0.25), 10, CYAN, True, PP_ALIGN.RIGHT)
    return slide


def metric_card(slide, x, y, w, title, value, accent=CYAN, note="", h=1.28):
    rect(slide, Inches(x), Inches(y), Inches(w), Inches(h), NAVY_2, True)
    textbox(slide, title, Inches(x + 0.16), Inches(y + 0.11), Inches(w - 0.32), Inches(0.22), 9, MUTED, True)
    textbox(slide, value, Inches(x + 0.16), Inches(y + 0.39), Inches(w - 0.32), Inches(0.40), 22, accent, True)
    if note:
        textbox(slide, note, Inches(x + 0.16), Inches(y + 0.92), Inches(w - 0.32), Inches(0.22), 8, MUTED)


def add_bullets(slide, items, x, y, w, h, size=18, color=WHITE):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    for idx, item in enumerate(items):
        p = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
        p.text = "• " + item
        p.font.name = FONT
        p.font.size = Pt(size)
        p.font.color.rgb = color
        p.space_after = Pt(10)
    return box


def arrow(slide, x1, y1, x2, y2, color=CYAN, width=2.2):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    line.line.color.rgb = color
    line.line.width = Pt(width)
    line.line.end_arrowhead = True
    return line


def flow_box(slide, x, y, w, h, title, detail, accent=CYAN):
    rect(slide, Inches(x), Inches(y), Inches(w), Inches(h), NAVY_2, True)
    rect(slide, Inches(x), Inches(y), Inches(0.07), Inches(h), accent)
    textbox(slide, title, Inches(x + 0.17), Inches(y + 0.14), Inches(w - 0.3), Inches(0.28), 14, WHITE, True, PP_ALIGN.CENTER)
    textbox(slide, detail, Inches(x + 0.17), Inches(y + 0.51), Inches(w - 0.3), Inches(h - 0.61), 9, MUTED, False, PP_ALIGN.CENTER)


def hbar(slide, label, value_text, ratio, x, y, w, color, sub=""):
    textbox(slide, label, Inches(x), Inches(y), Inches(2.0), Inches(0.28), 12, WHITE, True)
    rect(slide, Inches(x + 2.05), Inches(y + 0.02), Inches(w), Inches(0.26), NAVY_3, True)
    rect(slide, Inches(x + 2.05), Inches(y + 0.02), Inches(max(0.08, w * ratio)), Inches(0.26), color, True)
    textbox(slide, value_text, Inches(x + 2.15 + w), Inches(y - 0.01), Inches(1.45), Inches(0.28), 11, color, True, PP_ALIGN.RIGHT)
    if sub:
        textbox(slide, sub, Inches(x), Inches(y + 0.31), Inches(2.8 + w), Inches(0.20), 8, MUTED)


def add_table(slide, rows, widths, x, y, w, h, font_size=13):
    table = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h)).table
    for idx, width in enumerate(widths):
        table.columns[idx].width = Inches(width)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = value
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY_3 if r == 0 else NAVY_2
            cell.margin_left = cell.margin_right = Inches(0.06)
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.CENTER
                p.font.name = FONT
                p.font.size = Pt(font_size)
                p.font.bold = r == 0
                p.font.color.rgb = WHITE
    return table


def build():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 1 — title and five-part story map
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = NAVY
    rect(slide, Inches(0), Inches(0), Inches(0.18), Inches(7.5), CYAN)
    textbox(slide, "AER 병목을 구조로 풀다", Inches(0.75), Inches(0.75), Inches(11.7), Inches(0.68), 36, WHITE, True)
    textbox(slide, "Cluster2 Steal-Buffer Polarity AER · 디지털 1차 설계 결과", Inches(0.78), Inches(1.52), Inches(11.5), Inches(0.38), 18, CYAN, True)
    pillars = [
        ("01", "병목 정의", RED), ("02", "핵심 구조", CYAN), ("03", "수치 개선", GREEN),
        ("04", "시각적 검증", BLUE), ("05", "CAV 확장", PURPLE),
    ]
    for i, (num, label, color) in enumerate(pillars):
        x = 0.78 + i * 2.46
        rect(slide, Inches(x), Inches(2.45), Inches(2.18), Inches(1.27), NAVY_2, True)
        textbox(slide, num, Inches(x + 0.16), Inches(2.61), Inches(0.55), Inches(0.28), 15, color, True)
        textbox(slide, label, Inches(x + 0.16), Inches(3.04), Inches(1.82), Inches(0.34), 16, WHITE, True)
    rect(slide, Inches(0.78), Inches(4.45), Inches(11.98), Inches(1.14), NAVY_2, True)
    textbox(slide, "한 줄 결론", Inches(1.02), Inches(4.67), Inches(1.25), Inches(0.27), 11, MUTED, True)
    textbox(slide, "1-event/cycle 직렬화를 2-lane row-bitmap retire로 완화하고, 최종 polarity RTL의 기능·P&R 가능성과 CAV 소프트웨어 확장 경로를 증명", Inches(2.30), Inches(4.62), Inches(10.05), Inches(0.55), 18, GREEN, True, PP_ALIGN.CENTER)
    textbox(slide, "2026-08-28 · Xcelium / Genus / Innovus", Inches(0.80), Inches(6.78), Inches(11.2), Inches(0.25), 11, MUTED)

    # 2 — the primary bottleneck
    slide = base_slide(prs, "핵심 문제: burst 입력이 scalar retire에서 직렬화된다", 2, "01  병목 정의", "Source: recovered Fovea Xcelium full50 diagnostics · docs/K2_디지털개발_최종현황_20260813.txt")
    flow_box(slide, 0.70, 1.30, 2.30, 1.20, "16개 비동기 source", "동시에 여러 event 발생", BLUE)
    flow_box(slide, 4.00, 1.30, 2.30, 1.20, "공유 arbitration", "대기열과 경쟁 집중", ORANGE)
    flow_box(slide, 7.30, 1.30, 2.30, 1.20, "scalar output", "cycle당 1 event", RED)
    flow_box(slide, 10.25, 1.30, 2.30, 1.20, "결과", "local full → overrun", RED)
    arrow(slide, 3.02, 1.90, 3.94, 1.90, ORANGE)
    arrow(slide, 6.32, 1.90, 7.24, 1.90, ORANGE)
    arrow(slide, 9.62, 1.90, 10.18, 1.90, RED)
    metric_card(slide, 0.75, 3.10, 3.55, "FOVEA FULL50 ACCEPTED", "78,229 / 106,416", ORANGE, "acceptance 73.51%")
    metric_card(slide, 4.55, 3.10, 3.55, "SOURCE OVERRUN", "28,187", RED, "짧은 burst를 source-local capacity가 흡수 못함")
    metric_card(slide, 8.35, 3.10, 3.55, "FIXED-WINDOW EPC", "0.673901", RED, "1-event/cycle 경계 아래")
    textbox(slide, "병목의 본질", Inches(0.78), Inches(5.10), Inches(1.50), Inches(0.28), 12, CYAN, True)
    textbox(slide, "입력 발생률 ≠ retire 서비스율  →  대기 누적  →  source-local overrun", Inches(2.15), Inches(4.98), Inches(9.85), Inches(0.48), 23, WHITE, True, PP_ALIGN.CENTER)
    textbox(slide, "※ 수치는 회수된 scalar Fovea 기준선이며 최종 polarity-v1 trace와 다른 비교 캠페인", Inches(1.0), Inches(6.20), Inches(11.2), Inches(0.35), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 3 — bottleneck taxonomy
    slide = base_slide(prs, "AER의 병목은 하나가 아니라 연쇄적으로 발생한다", 3, "01  병목 정의", "Source: RTL contract and AER bottleneck coverage audit")
    bottlenecks = [
        ("① 직렬화", "여러 source event를\n한 cycle에 하나만 retire", RED),
        ("② 국소 포화", "대기 중 동일 source 재발생 시\nbuffer full/overrun", ORANGE),
        ("③ 클래스 불균형", "center 또는 peripheral만 몰리면\n고정 lane이 놀 수 있음", PURPLE),
        ("④ polarity 정렬", "주소와 polarity가 다른 시점에\npop되면 event 의미 훼손", BLUE),
        ("⑤ 시스템 시간축", "occurrence와 retire를 섞으면\nCAV geometry 인과가 왜곡", CYAN),
    ]
    for i, (title, detail, color) in enumerate(bottlenecks):
        x = 0.70 + (i % 3) * 4.15
        y = 1.25 if i < 3 else 3.72
        w = 3.72 if i < 3 else 5.80
        if i >= 3:
            x = 0.70 + (i - 3) * 6.15
        rect(slide, Inches(x), Inches(y), Inches(w), Inches(1.75), NAVY_2, True)
        rect(slide, Inches(x), Inches(y), Inches(0.08), Inches(1.75), color)
        textbox(slide, title, Inches(x + 0.22), Inches(y + 0.18), Inches(w - 0.4), Inches(0.32), 16, color, True)
        textbox(slide, detail, Inches(x + 0.22), Inches(y + 0.65), Inches(w - 0.4), Inches(0.72), 13, WHITE)
    textbox(slide, "이번 RTL은 ①~④를 직접 다루고, ⑤는 sidecar 기반 CAV 검증에서 의미를 분리", Inches(0.80), Inches(6.20), Inches(11.65), Inches(0.40), 16, GREEN, True, PP_ALIGN.CENTER)

    # 4 — architecture visual
    slide = base_slide(prs, "핵심 구조: source-local 흡수 + row-bitmap 2-lane retire", 4, "02  핵심 구조", "Source: final polarity RTL SHA-256 20d601a9…")
    # 4x4 sensor/source grid
    textbox(slide, "16 SOURCES", Inches(0.62), Inches(1.10), Inches(2.45), Inches(0.28), 11, MUTED, True, PP_ALIGN.CENTER)
    for r in range(4):
        for c in range(4):
            color = CYAN if r in (1, 2) else BLUE
            rect(slide, Inches(0.76 + c * 0.55), Inches(1.55 + r * 0.55), Inches(0.42), Inches(0.42), NAVY_3, True, color)
            textbox(slide, str(r * 4 + c), Inches(0.80 + c * 0.55), Inches(1.64 + r * 0.55), Inches(0.34), Inches(0.18), 8, color, True, PP_ALIGN.CENTER)
    arrow(slide, 3.20, 2.60, 4.05, 2.60)
    flow_box(slide, 4.10, 1.35, 2.25, 2.52, "16 × depth-2 FIFO", "pending count\nfront/back polarity\nsource별 burst 1회 추가 흡수", CYAN)
    arrow(slide, 6.40, 2.60, 7.23, 2.60)
    flow_box(slide, 7.30, 1.35, 2.05, 1.05, "CENTER", "row 1 / row 2", CYAN)
    flow_box(slide, 7.30, 2.82, 2.05, 1.05, "PERIPHERAL", "row 0 / row 3", BLUE)
    arrow(slide, 9.40, 1.88, 10.18, 1.88, CYAN)
    arrow(slide, 9.40, 3.35, 10.18, 3.35, BLUE)
    flow_box(slide, 10.25, 1.28, 2.25, 1.20, "RETIRE LANE 0", "row + 4b col_mask + 4b pol_mask", GREEN)
    flow_box(slide, 10.25, 2.75, 2.25, 1.20, "RETIRE LANE 1", "row + 4b col_mask + 4b pol_mask", GREEN)
    rect(slide, Inches(0.75), Inches(4.65), Inches(11.75), Inches(1.27), NAVY_2, True)
    textbox(slide, "표현 능력", Inches(0.97), Inches(4.87), Inches(1.22), Inches(0.25), 11, MUTED, True)
    textbox(slide, "1 lane × 4 columns  →  2 lanes × 4 columns  =  최대 8 events/cycle", Inches(2.20), Inches(4.78), Inches(9.78), Inches(0.44), 22, GREEN, True, PP_ALIGN.CENTER)
    textbox(slide, "col_mask가 1인 위치의 pol_mask만 유효 event polarity", Inches(2.35), Inches(5.35), Inches(9.45), Inches(0.28), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 5 — ideas mapped to problems
    slide = base_slide(prs, "각 병목에 대응하는 다섯 가지 RTL 아이디어", 5, "02  핵심 구조", "Source: polarity RTL lines 33–153 · arbiter2/arbiter4_tree")
    rows = [
        ("DEPTH-2 / SOURCE", "동일 source의 두 번째 event까지 저장", "국소 포화 완화", BLUE),
        ("ROW BITMAP", "선택된 row의 최대 4 columns를 동시에 pop", "scalar 직렬화 완화", CYAN),
        ("2 CLASS LANES", "center와 peripheral을 독립 arbitration", "경쟁 지점 분산", GREEN),
        ("CONDITIONAL STEAL", "반대 class가 idle일 때 두 row를 양 lane에 배치", "클래스 불균형 완화", PURPLE),
        ("POLARITY LOCKSTEP", "occupancy push/pop과 polarity FIFO를 동일 제어", "주소–polarity 정렬", ORANGE),
    ]
    for i, (tag, idea, result, color) in enumerate(rows):
        y = 1.18 + i * 1.06
        rect(slide, Inches(0.72), Inches(y), Inches(2.18), Inches(0.78), NAVY_3, True)
        textbox(slide, tag, Inches(0.82), Inches(y + 0.23), Inches(1.98), Inches(0.24), 11, color, True, PP_ALIGN.CENTER)
        arrow(slide, 2.96, y + 0.39, 3.55, y + 0.39, color)
        rect(slide, Inches(3.65), Inches(y), Inches(5.05), Inches(0.78), NAVY_2, True)
        textbox(slide, idea, Inches(3.83), Inches(y + 0.18), Inches(4.68), Inches(0.38), 14, WHITE, True, PP_ALIGN.CENTER)
        arrow(slide, 8.76, y + 0.39, 9.34, y + 0.39, color)
        rect(slide, Inches(9.45), Inches(y), Inches(3.10), Inches(0.78), NAVY_2, True, color)
        textbox(slide, result, Inches(9.60), Inches(y + 0.20), Inches(2.78), Inches(0.34), 13, color, True, PP_ALIGN.CENTER)
    textbox(slide, "제약: shared elastic buffer 아님 · downstream ready 없음 · full source의 same-cycle pop+arrival도 overrun", Inches(0.90), Inches(6.52), Inches(11.5), Inches(0.25), 10, ORANGE, True, PP_ALIGN.CENTER)

    # 6 — numeric structural comparison
    slide = base_slide(prs, "동일 회수 workload에서 구조 병목이 실제로 줄었다", 6, "03  수치 개선", "Source: recovered actual Xcelium full50 diagnostics; scalar Fovea vs original Cluster2 architecture family")
    textbox(slide, "Accepted events", Inches(0.72), Inches(1.16), Inches(3.0), Inches(0.28), 16, WHITE, True)
    hbar(slide, "Scalar Fovea", "78,229", 78229 / 106416, 0.75, 1.66, 6.80, ORANGE, "73.51% of generated")
    hbar(slide, "Cluster2", "94,157", 94157 / 106416, 0.75, 2.38, 6.80, GREEN, "88.48% of generated")
    metric_card(slide, 10.12, 1.52, 2.35, "ACCEPTED", "+20.4%", GREEN, "+15,928 events")
    textbox(slide, "Source overrun", Inches(0.72), Inches(3.22), Inches(3.0), Inches(0.28), 16, WHITE, True)
    hbar(slide, "Scalar Fovea", "28,187", 1.0, 0.75, 3.72, 6.80, RED)
    hbar(slide, "Cluster2", "12,259", 12259 / 28187, 0.75, 4.44, 6.80, CYAN)
    metric_card(slide, 10.12, 3.62, 2.35, "OVERRUN", "−56.5%", GREEN, "15,928 fewer")
    metric_card(slide, 0.75, 5.58, 3.55, "FOVEA EPC", "0.673901", ORANGE, "fixed window")
    metric_card(slide, 4.56, 5.58, 3.55, "CLUSTER2 EPC", "0.811620", CYAN, "fixed window")
    metric_card(slide, 8.37, 5.58, 3.55, "THROUGHPUT", "+20.4%", GREEN, "same recovered campaign")
    textbox(slide, "범위: 원본 구조군 비교 수치. 최종 steal-buffer polarity-v1의 exact head-to-head 개선율로 재해석하지 않음.", Inches(0.86), Inches(6.88), Inches(11.5), Inches(0.20), 8, ORANGE, True, PP_ALIGN.CENTER)

    # 7 — final RTL conservation proof
    slide = base_slide(prs, "최종 polarity-v1 RTL은 8,503 events를 끝까지 보존했다", 7, "03  수치 개선", "Source: polarity_v1_release_receipt.json · Xcelium 23.09-s013")
    metric_card(slide, 0.70, 1.28, 2.28, "GENERATED", "8,503", BLUE)
    metric_card(slide, 3.13, 1.28, 2.28, "DELIVERED", "8,503", GREEN)
    metric_card(slide, 5.56, 1.28, 2.28, "OVERRUN", "0", GREEN)
    metric_card(slide, 7.99, 1.28, 2.28, "PHANTOM", "0", GREEN)
    metric_card(slide, 10.42, 1.28, 2.28, "DUPLICATE", "0", GREEN)
    rect(slide, Inches(0.78), Inches(3.05), Inches(11.82), Inches(1.18), NAVY_2, True)
    textbox(slide, "보존 법칙", Inches(1.05), Inches(3.36), Inches(1.25), Inches(0.30), 12, MUTED, True)
    textbox(slide, "generated  =  delivered + overrun  =  8,503", Inches(2.35), Inches(3.22), Inches(9.65), Inches(0.48), 25, GREEN, True, PP_ALIGN.CENTER)
    flow_box(slide, 0.92, 4.78, 3.15, 1.13, "독립 ledger 재검증", "TB 외 Python 경로 · order_violations=0", CYAN)
    flow_box(slide, 5.08, 4.78, 3.15, 1.13, "drain 후 empty", "모든 source FIFO 비움", GREEN)
    flow_box(slide, 9.24, 4.78, 3.15, 1.13, "polarity 보존", "선택된 col_mask 위치와 일치", PURPLE)
    textbox(slide, "한계: 동일 source·동일 polarity event의 독립 event-ID 순서 식별은 주장하지 않음", Inches(0.92), Inches(6.35), Inches(11.45), Inches(0.28), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 8 — timing/PPA visual
    slide = base_slide(prs, "P&R sweep으로 285.714 MHz clean point를 확인", 8, "04  시각적 검증", "Source: Innovus slow 0.9 V 125 °C setup/hold reports · upstream 9b0d951")
    rows = [
        ["Period", "Frequency", "Setup", "Hold", "판정"],
        ["4.5 ns", "222.222 MHz", "+1.349 ns", "+0.166 ns", "PASS"],
        ["4.0 ns", "250.000 MHz", "+0.849 ns", "+0.166 ns", "PASS"],
        ["3.5 ns", "285.714 MHz", "+0.454 ns", "+0.167 ns", "PASS"],
        ["3.0 ns", "333.333 MHz", "−0.004 ns", "+0.169 ns", "FAIL"],
    ]
    table = add_table(slide, rows, [1.55, 2.25, 1.70, 1.70, 1.40], 0.67, 1.30, 8.60, 3.70, 12)
    table.cell(3, 4).fill.fore_color.rgb = RGBColor(23, 91, 66)
    table.cell(4, 4).fill.fore_color.rgb = RGBColor(120, 36, 54)
    metric_card(slide, 9.58, 1.28, 2.80, "CLEAN POINT", "285.714 MHz", GREEN, "setup +0.454 · hold +0.167")
    metric_card(slide, 9.58, 2.83, 2.80, "AREA RAW", "1254.114", CYAN, "596 instances")
    metric_card(slide, 9.58, 4.38, 2.80, "VECTORLESS POWER", "0.107389 mW", ORANGE, "default activity 0.2")
    rect(slide, Inches(0.78), Inches(5.42), Inches(8.45), Inches(0.13), NAVY_3, True)
    points = [(1.15, GREEN), (3.20, GREEN), (5.42, GREEN), (7.95, RED)]
    labels = ["222", "250", "285.7", "333.3 MHz"]
    for (px, color), label in zip(points, labels):
        rect(slide, Inches(px), Inches(5.29), Inches(0.28), Inches(0.28), color, True)
        textbox(slide, label, Inches(px - 0.35), Inches(5.72), Inches(1.0), Inches(0.25), 9, color, True, PP_ALIGN.CENTER)
    textbox(slide, "285.714 MHz PASS / 다음 측정점 333.333 MHz FAIL · 중간 주파수 미측정", Inches(0.85), Inches(6.35), Inches(8.3), Inches(0.28), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 9 — physical readability slide
    slide = base_slide(prs, "구조가 실제 표준셀 배치까지 이어지는지 확인", 9, "04  시각적 검증", "Source: 3.5 ns layout image · area/power/internal physical reports")
    if LAYOUT.exists():
        slide.shapes.add_picture(str(LAYOUT), Inches(0.72), Inches(1.25), width=Inches(6.35), height=Inches(5.45))
    metric_card(slide, 7.45, 1.30, 4.75, "POST-ROUTE AREA RAW", "1254.114", CYAN, "596 instances · report unit not stated")
    metric_card(slide, 7.45, 2.84, 4.75, "POWER BREAKDOWN", "71.2% internal", BLUE, "28.8% switching · 0.025% leakage")
    metric_card(slide, 7.45, 4.38, 4.75, "INTERNAL CHECKS", "DRC 0 · antenna 0", GREEN, "internal reports; foundry signoff 아님")
    textbox(slide, "Non-OCV · SI off · No SPEF/RCDB · ideal-clock/no-drive warnings 공개", Inches(7.44), Inches(6.17), Inches(4.80), Inches(0.40), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 10 — CAV extension
    slide = base_slide(prs, "별도 address-only track에서 CAV software 확장 경로 확인", 10, "05  CAV 확장", "Source: official_uzh_cluster2_cav_result.json · sealed legacy address-only bridge authority")
    flow_box(slide, 0.55, 1.25, 2.12, 1.20, "UZH events + pose", "official shapes_rotation", BLUE)
    flow_box(slide, 3.05, 1.25, 2.12, 1.20, "Cluster2 outcome", "8,503 exact identity join", CYAN)
    flow_box(slide, 5.55, 1.25, 2.12, 1.20, "causal-CAV", "occurrence time geometry", PURPLE)
    flow_box(slide, 8.05, 1.25, 2.12, 1.20, "WORLD rays", "8,420 disposition", GREEN)
    flow_box(slide, 10.55, 1.25, 2.12, 1.20, "512 × 256 grid", "821 unique cells", ORANGE)
    for x in [2.70, 5.20, 7.70, 10.20]:
        arrow(slide, x, 1.85, x + 0.30, 1.85)
    metric_card(slide, 0.70, 3.05, 2.75, "JOIN", "8,503 / 8,503", GREEN, "event identity exact")
    metric_card(slide, 3.75, 3.05, 2.75, "WORLD", "8,420", CYAN, "disposition 99.02% · success율 아님")
    metric_card(slide, 6.80, 3.05, 2.75, "SENSOR_FIXED", "83", ORANGE, "0.98% explicit bypass")
    metric_card(slide, 9.85, 3.05, 2.75, "GRID CELLS", "821", PURPLE, "occupied x 238–298 · y 93–165")
    rect(slide, Inches(0.78), Inches(4.85), Inches(11.75), Inches(1.05), NAVY_2, True)
    textbox(slide, "시간 의미 분리", Inches(0.98), Inches(5.12), Inches(1.55), Inches(0.28), 11, MUTED, True)
    textbox(slide, "occurrence timestamp → geometry     |     retire cycle → transport latency sidecar", Inches(2.45), Inches(5.04), Inches(9.55), Inches(0.42), 18, WHITE, True, PP_ALIGN.CENTER)
    textbox(slide, "최종 polarity-v1과 독립된 legacy 결과 · full-population polarity→CAV, wire-complete RTL 및 PPA는 HOLD", Inches(0.82), Inches(6.38), Inches(11.60), Inches(0.30), 11, ORANGE, True, PP_ALIGN.CENTER)

    # 11 — conclusion
    slide = base_slide(prs, "결론: 병목–구조–수치–물리–확장 근거를 한 흐름으로 닫았다", 11, "SUMMARY", "Source bundle: receipts · raw reports · SHA256SUMS · PROVENANCE.json")
    conclusions = [
        ("01 병목", "scalar 직렬화와 source overrun", RED),
        ("02 구조", "depth-2/source + bitmap 2 lanes + steal + polarity", CYAN),
        ("03 수치", "구조군 EPC +20.4% · overrun −56.5%", GREEN),
        ("04 물리", "8,503/8,503 보존 · 285.714 MHz clean", BLUE),
        ("05 확장", "legacy addr-only: 8,503 join → 8,420 WORLD", PURPLE),
    ]
    for i, (tag, text, color) in enumerate(conclusions):
        y = 1.18 + i * 0.92
        rect(slide, Inches(0.74), Inches(y), Inches(1.55), Inches(0.66), NAVY_3, True)
        textbox(slide, tag, Inches(0.84), Inches(y + 0.20), Inches(1.35), Inches(0.24), 11, color, True, PP_ALIGN.CENTER)
        rect(slide, Inches(2.48), Inches(y), Inches(6.55), Inches(0.66), NAVY_2, True)
        textbox(slide, text, Inches(2.66), Inches(y + 0.16), Inches(6.18), Inches(0.30), 14, WHITE, True)
    metric_card(slide, 9.43, 1.18, 2.95, "GO", "native AER RTL", GREEN, "functional + conditional PPA")
    metric_card(slide, 9.43, 2.78, 2.95, "GO", "software CAV path", CYAN, "pinned functional feasibility")
    metric_card(slide, 9.43, 4.38, 2.95, "NEXT / HOLD", "CAV RTL + signoff", ORANGE, "exact Fmax · activity power")
    textbox(slide, "1차 제출: RTL · TB · synthesis · timing · area · power · frequency · raw evidence", Inches(0.82), Inches(6.35), Inches(11.55), Inches(0.33), 14, GREEN, True, PP_ALIGN.CENTER)

    prs.core_properties.title = "AER 병목을 구조로 풀다 — Cluster2 Polarity AER"
    prs.core_properties.subject = "AER bottlenecks, architecture, quantified improvement, physical evidence, CAV extensibility"
    prs.core_properties.author = "AI-semi team"
    prs.core_properties.keywords = "Cluster2, AER, polarity, bottleneck, Genus, Innovus, CAV"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    print(f"WROTE {OUT} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    build()
