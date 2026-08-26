#!/usr/bin/env python3
"""Build the Korean first-round Cluster2 digital-result presentation."""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx"
LAYOUT = ROOT / "evidence" / "ppa" / "upstream_9b0d951" / "layout_3.5.png"

NAVY = RGBColor(10, 25, 47)
NAVY_2 = RGBColor(18, 43, 70)
CYAN = RGBColor(39, 207, 202)
BLUE = RGBColor(65, 141, 255)
WHITE = RGBColor(245, 248, 252)
MUTED = RGBColor(174, 192, 211)
GREEN = RGBColor(59, 201, 134)
RED = RGBColor(255, 99, 115)
ORANGE = RGBColor(255, 181, 71)
FONT = "Malgun Gothic"


def rect(slide, x, y, w, h, color, radius=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def textbox(slide, text, x, y, w, h, size=24, color=WHITE, bold=False,
            align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = valign
    p = frame.paragraphs[0]
    p.text = text
    p.alignment = align
    p.font.name = FONT
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.color.rgb = color
    return box


def base_slide(prs, title, number, source):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = NAVY
    rect(slide, Inches(0), Inches(0), Inches(0.13), Inches(7.5), CYAN)
    textbox(slide, title, Inches(0.55), Inches(0.30), Inches(11.9), Inches(0.55), 26, WHITE, True)
    rect(slide, Inches(0.55), Inches(0.94), Inches(12.2), Inches(0.02), CYAN)
    textbox(slide, source, Inches(0.58), Inches(7.08), Inches(11.4), Inches(0.22), 8, MUTED)
    textbox(slide, f"{number:02d}", Inches(12.1), Inches(6.98), Inches(0.55), Inches(0.28), 10, CYAN, True, PP_ALIGN.RIGHT)
    return slide


def add_bullets(slide, items, x=0.75, y=1.3, w=11.7, h=5.45, size=22):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    for idx, item in enumerate(items):
        p = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
        p.text = item
        p.level = 0
        p.font.name = FONT
        p.font.size = Pt(size)
        p.font.color.rgb = WHITE
        p.space_after = Pt(13)
        p.text = "• " + p.text
    return box


def metric_card(slide, x, y, w, title, value, accent=CYAN, note=""):
    rect(slide, Inches(x), Inches(y), Inches(w), Inches(1.35), NAVY_2, True)
    textbox(slide, title, Inches(x + 0.18), Inches(y + 0.13), Inches(w - 0.36), Inches(0.25), 11, MUTED, True)
    textbox(slide, value, Inches(x + 0.18), Inches(y + 0.43), Inches(w - 0.36), Inches(0.43), 24, accent, True)
    if note:
        textbox(slide, note, Inches(x + 0.18), Inches(y + 0.97), Inches(w - 0.36), Inches(0.22), 9, MUTED)


def add_table(slide, rows, widths, x, y, w, h, font_size=14):
    table = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h)).table
    for idx, width in enumerate(widths):
        table.columns[idx].width = Inches(width)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = value
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY_2 if r else RGBColor(28, 74, 104)
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.08)
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

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = NAVY
    rect(slide, Inches(0), Inches(0), Inches(0.18), Inches(7.5), CYAN)
    textbox(slide, "Cluster2 Polarity AER", Inches(0.8), Inches(1.35), Inches(11.8), Inches(0.8), 38, WHITE, True)
    textbox(slide, "디지털 1차 설계 결과", Inches(0.82), Inches(2.22), Inches(10), Inches(0.5), 25, CYAN, True)
    textbox(slide, "RTL · Synthesis · Timing · Area · Power · Operating Frequency", Inches(0.82), Inches(3.05), Inches(11.5), Inches(0.45), 18, MUTED)
    rect(slide, Inches(0.82), Inches(4.05), Inches(11.55), Inches(1.25), NAVY_2, True)
    textbox(slide, "285.714 MHz post-route clean point", Inches(1.12), Inches(4.30), Inches(10.9), Inches(0.42), 25, GREEN, True, PP_ALIGN.CENTER)
    textbox(slide, "2026-08-28 · Xcelium / Genus / Innovus", Inches(0.82), Inches(6.75), Inches(11.4), Inches(0.3), 12, MUTED)

    slide = base_slide(prs, "제출 범위와 결론", 2, "Source: final integration fbb053f · PPA source 9b0d951")
    metric_card(slide, 0.75, 1.35, 2.8, "FUNCTIONAL", "8,503 / 8,503", GREEN, "generated / delivered")
    metric_card(slide, 3.75, 1.35, 2.8, "OPERATING POINT", "285.714 MHz", GREEN, "3.5 ns setup/hold PASS")
    metric_card(slide, 6.75, 1.35, 2.8, "P&R AREA RAW", "1254.114", CYAN, "596 instances")
    metric_card(slide, 9.75, 1.35, 2.8, "VECTORLESS POWER", "0.10738887 mW", ORANGE, "default activity 0.2")
    add_bullets(slide, [
        "polarity를 포함한 최종 RTL과 합성/P&R top을 동일하게 고정",
        "4.5 → 4.0 → 3.5 → 3.0 ns sweep으로 timing 경계 확인",
        "exact Fmax와 workload-activity power는 후속 과제로 명시",
    ], y=3.25, h=2.8, size=20)

    slide = base_slide(prs, "설계 구조", 3, "Source: final polarity RTL SHA-256 20d601a9…")
    labels = [
        ("16 event sources\narrival + polarity", 0.7, BLUE),
        ("source별 depth-2\nevent/polarity FIFO", 3.55, CYAN),
        ("row arbitration\ncenter / peripheral", 6.40, ORANGE),
        ("2 retire lanes\nrow + col/pol mask", 9.25, GREEN),
    ]
    for idx, (label, x, color) in enumerate(labels):
        rect(slide, Inches(x), Inches(2.15), Inches(2.35), Inches(1.45), NAVY_2, True)
        rect(slide, Inches(x), Inches(2.15), Inches(0.08), Inches(1.45), color)
        textbox(slide, label, Inches(x + 0.18), Inches(2.37), Inches(1.98), Inches(0.95), 18, WHITE, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        if idx < len(labels) - 1:
            textbox(slide, "→", Inches(x + 2.38), Inches(2.56), Inches(0.42), Inches(0.45), 25, MUTED, True, PP_ALIGN.CENTER)
    add_bullets(slide, [
        "cycle당 최대 8 event를 두 row-bitmap lane으로 표현",
        "선택된 col_mask bit만 같은 위치의 pol_mask polarity를 가짐",
        "event identity와 timestamp는 검증 sidecar이며 DUT 입력 선택에 사용되지 않음",
    ], y=4.35, h=2.0, size=18)

    slide = base_slide(prs, "RTL 기능 검증", 4, "Source: polarity_v1_release_receipt.json · Xcelium 23.09-s013")
    metric_card(slide, 0.8, 1.35, 2.8, "GENERATED", "8,503", GREEN)
    metric_card(slide, 3.75, 1.35, 2.8, "DELIVERED", "8,503", GREEN)
    metric_card(slide, 6.70, 1.35, 2.8, "OVERRUN", "0", GREEN)
    metric_card(slide, 9.65, 1.35, 2.8, "PHANTOM / DUP", "0 / 0", GREEN)
    add_bullets(slide, [
        "drain 후 empty=true",
        "raw trace와 cycle ledger를 독립 verifier로 보존성 재검증",
        "claim limit: identity-order independence는 주장하지 않음",
    ], y=3.25, h=2.8, size=19)

    slide = base_slide(prs, "Synthesis 조건", 5, "Source: genus_3.5.tcl · 3.5_area/gtiming/gpower reports")
    rows = [
        ["항목", "조건 / 결과"],
        ["Tool", "Genus 23.14-s090_1"],
        ["Library / corner", "GPDK045 slow · 0.9 V · 125 °C"],
        ["Constraint", "3.5 ns · uncertainty 0.100 ns · I/O delay 0.250 ns"],
        ["Mapped area", "1156.644 · 544 cells"],
        ["Mapped setup", "+1.125 ns"],
        ["Vectorless power", "0.0505898 mW"],
    ]
    add_table(slide, rows, [3.0, 8.5], 0.9, 1.35, 11.5, 4.8, 15)
    textbox(slide, "합성 netlist와 output SDC를 Innovus 입력으로 사용", Inches(0.95), Inches(6.35), Inches(11.3), Inches(0.35), 15, CYAN, True, PP_ALIGN.CENTER)

    slide = base_slide(prs, "Timing 최적화 sweep", 6, "Source: Innovus setup/hold reports · slow view")
    rows = [
        ["Period", "Frequency", "Setup", "Hold", "Result"],
        ["4.5 ns", "222.222 MHz", "+1.349 ns", "+0.166 ns", "PASS"],
        ["4.0 ns", "250.000 MHz", "+0.849 ns", "+0.166 ns", "PASS"],
        ["3.5 ns", "285.714 MHz", "+0.454 ns", "+0.167 ns", "PASS"],
        ["3.0 ns", "333.333 MHz", "−0.004 ns", "+0.169 ns", "FAIL"],
    ]
    table = add_table(slide, rows, [2.0, 2.6, 2.2, 2.2, 2.0], 0.75, 1.45, 11.0, 3.7, 15)
    table.cell(4, 4).fill.fore_color.rgb = RGBColor(120, 36, 54)
    table.cell(3, 4).fill.fore_color.rgb = RGBColor(23, 91, 66)
    metric_card(slide, 1.0, 5.55, 5.1, "DEFENSIBLE CLAIM", "285.714 MHz clean operating point", GREEN)
    metric_card(slide, 7.0, 5.55, 5.1, "CLAIM LIMIT", "exact Fmax 미확정", ORANGE, "관측 bracket [285.714, 333.333) MHz")

    slide = base_slide(prs, "Post-route area와 layout", 7, "Source: 3.5_pnr_area.rpt · layout_3.5.png")
    if LAYOUT.exists():
        slide.shapes.add_picture(str(LAYOUT), Inches(0.75), Inches(1.35), width=Inches(6.2), height=Inches(5.3))
    metric_card(slide, 7.35, 1.55, 4.9, "3.5 ns AREA RAW", "1254.114", CYAN, "596 instances")
    metric_card(slide, 7.35, 3.20, 4.9, "PHYSICAL CHECK", "DRC 0 · antenna 0", GREEN, "internal reports; signoff 아님")
    metric_card(slide, 7.35, 4.85, 4.9, "FLOORPLAN", "AR 1.0 · util 0.5", BLUE, "core margin 10")
    textbox(slide, "report에 면적 단위가 명시되지 않아 area raw로 표기", Inches(7.35), Inches(6.45), Inches(4.9), Inches(0.35), 12, ORANGE, True, PP_ALIGN.CENTER)

    slide = base_slide(prs, "Power 결과", 8, "Source: 3.5_pnr_power.rpt · Power Units = 1mW")
    metric_card(slide, 0.8, 1.40, 3.65, "INTERNAL", "0.07647953 mW", CYAN, "71.2174%")
    metric_card(slide, 4.85, 1.40, 3.65, "SWITCHING", "0.03088277 mW", BLUE, "28.7579%")
    metric_card(slide, 8.90, 1.40, 3.65, "LEAKAGE", "0.00002657 mW", ORANGE, "0.0247%")
    rect(slide, Inches(1.55), Inches(3.35), Inches(10.2), Inches(1.25), NAVY_2, True)
    textbox(slide, "TOTAL  0.10738887 mW", Inches(1.8), Inches(3.68), Inches(9.7), Inches(0.48), 29, GREEN, True, PP_ALIGN.CENTER)
    textbox(slide, "Vectorless estimate · sequential/primary-input activity 0.2", Inches(1.2), Inches(5.05), Inches(10.9), Inches(0.42), 18, ORANGE, True, PP_ALIGN.CENTER)
    textbox(slide, "VCD/SAIF workload power 또는 energy/event로 해석하지 않음", Inches(1.2), Inches(5.60), Inches(10.9), Inches(0.42), 16, MUTED, False, PP_ALIGN.CENTER)

    slide = base_slide(prs, "결론과 근거 경계", 9, "Source bundle: SHA256SUMS · PROVENANCE.json")
    metric_card(slide, 0.8, 1.35, 3.7, "GO", "RTL + functional", GREEN, "polarity-v1 release gate")
    metric_card(slide, 4.82, 1.35, 3.7, "GO", "285.714 MHz", GREEN, "post-route clean point")
    metric_card(slide, 8.84, 1.35, 3.7, "HOLD", "signoff / exact Fmax", ORANGE, "activity-based power 포함")
    add_bullets(slide, [
        "제출 폴더에 RTL, TB, netlist, SDC/TCL, raw reports, receipts, checksums 포함",
        "check_timing의 ideal-clock 1 / no-drive 34 경고와 write_db 오류를 숨기지 않음",
        "No SPEF/RCDB, Non-OCV, SI off이므로 silicon/foundry signoff 주장 금지",
        "1차 결과물로는 검증된 operating point와 조건부 PPA를 제출",
    ], y=3.35, h=2.8, size=18)

    prs.core_properties.title = "Cluster2 Polarity AER 디지털 1차 설계 결과"
    prs.core_properties.subject = "RTL, synthesis, timing, area, power, operating frequency"
    prs.core_properties.author = "AI-semi team"
    prs.core_properties.keywords = "Cluster2, AER, polarity, Genus, Innovus"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    build()
