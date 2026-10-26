"""String Art Manual Weaving Script & PDF Generator.

Decodes CNC G-Code (.nc files) into linear, line-by-line hand-weaving instructions
and generates an elegant, printable PDF manual with narrow 0.5-inch margins,
protective outline page borders, and checkbox trackers.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from services.storage import storage


class NumberedCanvasWithBorder(canvas.Canvas):
    """Two-pass canvas that renders:
    1. Elegant outer and inner outline borders (matching 0.5 in margin).
    2. Header and 'Page X of Y' dynamic footers.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        w, h = letter

        # 1. Double Outline Border around the 0.5-inch page margin
        # Outer border
        self.setStrokeColor(colors.HexColor("#CBD5E1"))  # Slate-300
        self.setLineWidth(1.0)
        self.rect(26, 26, w - 52, h - 52)

        # Inner thin accent border
        self.setStrokeColor(colors.HexColor("#E2E8F0"))  # Slate-200
        self.setLineWidth(0.5)
        self.rect(29, 29, w - 58, h - 58)

        # 2. Running Header (on page 2 and later)
        if self._pageNumber > 1:
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawString(38, h - 22, "String Art Manual Instructions • Step-by-Step Weaving Guide")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(38, h - 25, w - 38, h - 25)

        # 3. Running Footer with 'Page X of Y'
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        footer_text = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(w - 38, 16, footer_text)
        self.drawString(38, 16, "Keep thread under steady tension • Check each step as you weave")

        self.restoreState()


def decode_gcode_to_steps(gcode_text: str, num_pins: int = 240) -> List[Dict[str, Union[int, str]]]:
    """Decodes G-Code into clean, 1-based physical pin steps (1 to 240).

    Uses the verified circular modulo formula:
        physical_pin = ((continuous_pin - 1) % num_pins + num_pins) % num_pins + 1
    """
    if not gcode_text:
        return []

    steps: List[Dict[str, Union[int, str]]] = []

    # Primary: Parse high-precision step comments: '; Step N: src -> dst | nails_passed=M'
    step_pattern = re.compile(
        r";\s*Step\s*(\d+):\s*(-?\d+)\s*->\s*(-?\d+)(?:\s*\|\s*nails_passed=(\d+))?",
        re.IGNORECASE,
    )

    matches = list(step_pattern.finditer(gcode_text))

    if matches:
        for m in matches:
            step_num = int(m.group(1))
            raw_from = int(m.group(2))
            raw_to = int(m.group(3))
            raw_passed = int(m.group(4)) if m.group(4) else abs(raw_to - raw_from)

            p_from = ((raw_from - 1) % num_pins + num_pins) % num_pins + 1
            p_to = ((raw_to - 1) % num_pins + num_pins) % num_pins + 1

            steps.append({
                "step": step_num,
                "from_pin": p_from,
                "to_pin": p_to,
                "nails_passed": raw_passed,
            })
    else:
        # Fallback: Parse G1 X positions
        move_pattern = re.compile(r"G1\s+F\d+\s+X(-?\d+(?:\.\d+)?)\s+Y-35", re.IGNORECASE)
        positions = [round(float(match.group(1))) for match in move_pattern.finditer(gcode_text)]

        if positions:
            current_pin = 1
            for idx, raw_to in enumerate(positions, start=1):
                p_to = ((int(raw_to) - 1) % num_pins + num_pins) % num_pins + 1
                passed = abs(p_to - current_pin)
                steps.append({
                    "step": idx,
                    "from_pin": current_pin,
                    "to_pin": p_to,
                    "nails_passed": passed,
                })
                current_pin = p_to

    return steps


def generate_manual_script_pdf(
    gcode_input: Union[str, Path],
    user_id: str,
    art_id: str,
    art_name: str = "String Art Project",
    num_pins: int = 240,
    thread_type: str = "0.16 mm",
    output_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Generates the manual hand-weaving PDF instructions with narrow 0.5-inch margins,
    outline borders, and linear step-by-step instructions.

    Saves to: storage/scripts/{user_id}/{art_id}.pdf
    """
    user_id_str = str(user_id)
    clean_art_id = str(art_id)

    # 1. Resolve G-Code text
    if isinstance(gcode_input, Path) or (isinstance(gcode_input, str) and "\n" not in gcode_input and os.path.exists(gcode_input)):
        gcode_path = Path(gcode_input)
        gcode_text = gcode_path.read_text(encoding="utf-8")
    else:
        gcode_text = str(gcode_input)

    # 2. Decode G-Code into physical pin steps
    steps = decode_gcode_to_steps(gcode_text, num_pins=num_pins)
    total_steps = len(steps)

    # 3. Determine output path
    if output_path:
        pdf_path = Path(output_path)
    else:
        pdf_path = storage.get_local_path(f"scripts/{user_id_str}/{clean_art_id}.pdf")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # 4. Initialize SimpleDocTemplate with Narrow margins (0.5 in = 36 pt)
    margin = 0.5 * inch  # 36 points
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    story = []

    # 5. Styles
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
        alignment=1,  # Center
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
        alignment=1,  # Center
        spaceAfter=12,
    )

    body_bold = ParagraphStyle(
        "BodyBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1E293B"),
    )

    table_header_center = ParagraphStyle(
        "TableHeaderCenter",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.white,
        alignment=1,  # Center
    )

    table_header_left = ParagraphStyle(
        "TableHeaderLeft",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.white,
        alignment=0,  # Left
    )

    body_regular = ParagraphStyle(
        "BodyRegular",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#334155"),
    )

    compass_text = ParagraphStyle(
        "CompassText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )

    # 6. Cover Header Card
    story.append(Paragraph("MANUAL STRING ART WEAVING GUIDE", title_style))
    story.append(
        Paragraph(
            f"Step-by-Step Nail Instructions for <b>{art_name}</b> • Hand Crafting Edition",
            subtitle_style,
        )
    )

    # Project Specifications Table
    specs_data = [
        [
            Paragraph("<b>Art Name:</b>", body_regular),
            Paragraph(art_name, body_bold),
            Paragraph("<b>Total Nails:</b>", body_regular),
            Paragraph(f"{num_pins} Pins", body_bold),
        ],
        [
            Paragraph("<b>Total Lines:</b>", body_regular),
            Paragraph(f"{total_steps:,} Steps", body_bold),
            Paragraph("<b>Thread Type:</b>", body_regular),
            Paragraph(thread_type, body_bold),
        ],
        [
            Paragraph("<b>Start Knot:</b>", body_regular),
            Paragraph("Pin 1 (Top / 12 o'clock)", body_bold),
            Paragraph("<b>Direction:</b>", body_regular),
            Paragraph("Clockwise Numbering (1 ➔ 240)", body_bold),
        ],
    ]

    specs_table = Table(specs_data, colWidths=[80, 180, 80, 180])
    specs_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#F1F5F9")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(specs_table)
    story.append(Spacer(1, 8))

    # Calibration Compass Quick Notice Box
    compass_html = (
        "<b>📌 Calibration Compass:</b> Number nails clockwise starting at 12 o'clock. "
        "• <b>12 o'clock:</b> Pin 1 / 240 &nbsp;|&nbsp; "
        "• <b>3 o'clock:</b> Pin 60 &nbsp;|&nbsp; "
        "• <b>6 o'clock:</b> Pin 120 &nbsp;|&nbsp; "
        "• <b>9 o'clock:</b> Pin 180<br/>"
        "<i>Tying off: Knot thread securely onto Pin 1, maintain steady tension, and check off each step as you complete it.</i>"
    )
    compass_p = Paragraph(compass_html, compass_text)
    compass_box = Table([[compass_p]], colWidths=[520])
    compass_box.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEFCE8")),  # Warm amber/light yellow
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#FDE047")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(compass_box)
    story.append(Spacer(1, 10))

    # 7. Linear Instructions Table (Line by line in flow)
    # Header row
    table_data = [
        [
            Paragraph("<b>Step #</b>", table_header_center),
            Paragraph("<b>Manual Weaving Path</b>", table_header_left),
            Paragraph("<b>Span / Nails Passed</b>", table_header_center),
            Paragraph("<b>Done</b>", table_header_center),
        ]
    ]

    for item in steps:
        s_num = item["step"]
        p_from = item["from_pin"]
        p_to = item["to_pin"]
        passed = item["nails_passed"]

        # Milestone highlighting every 500 steps
        is_milestone = (s_num % 500 == 0)

        step_col = Paragraph(f"<b>#{s_num:04d}</b>", body_regular)
        path_col = Paragraph(f"Pin <b>{p_from}</b> &nbsp;➔&nbsp; Pin <b>{p_to}</b>", body_bold)
        span_col = Paragraph(f"{passed} nails", body_regular)
        check_col = Paragraph("[ &nbsp; ]", body_bold)

        table_data.append([step_col, path_col, span_col, check_col])

    # Printable width: letter width is 612 - 72 (margins) = 540 pt
    # Col widths: 70, 270, 130, 70 = 540 pt
    steps_table = Table(table_data, colWidths=[70, 270, 130, 70], repeatRows=1)

    table_styles = [
        # Table Header
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),  # Dark Navy/Slate
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]

    # Alternate row striping for comfortable reading
    for row_idx in range(1, len(table_data)):
        if row_idx % 2 == 0:
            table_styles.append(
                ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#F8FAFC"))
            )
        # Highlight milestones
        if (row_idx) % 500 == 0:
            table_styles.append(
                ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#FEF3C7"))
            )

    steps_table.setStyle(TableStyle(table_styles))
    story.append(steps_table)

    # 8. Build PDF with NumberedCanvasWithBorder
    doc.build(story, canvasmaker=NumberedCanvasWithBorder)

    # Sync / upload to storage (Local or S3 Cloud)
    storage.upload_file(f"scripts/{user_id_str}/{clean_art_id}.pdf", pdf_path, content_type="application/pdf")

    print(f"[PDF SCRIPT GENERATED] Saved to: {pdf_path} ({pdf_path.stat().st_size:,} bytes | {total_steps} steps)")
    return pdf_path
