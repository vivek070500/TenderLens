from datetime import datetime

from fpdf import FPDF

from config import VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW

# Brand / UI-aligned palette (RGB 0–255)
_COLOR_NAVY = (30, 58, 138)
_COLOR_NAVY_LIGHT = (37, 99, 235)
_COLOR_TEXT = (15, 23, 42)
_COLOR_MUTED = (100, 116, 139)
_COLOR_BORDER = (226, 232, 240)
_COLOR_ROW_ALT = (248, 250, 252)
_COLOR_PASS_BG = (220, 252, 231)
_COLOR_FAIL_BG = (254, 226, 226)
_COLOR_REVIEW_BG = (254, 249, 195)
_COLOR_PASS_ACCENT = (22, 163, 74)
_COLOR_FAIL_ACCENT = (220, 38, 38)
_COLOR_REVIEW_ACCENT = (202, 138, 4)
_COLOR_HERO_MID = (29, 78, 216)
_COLOR_CHIP_ON_HERO = (255, 255, 255)


def _safe(text):
    """Replace Unicode characters unsupported by FPDF built-in fonts with ASCII equivalents."""
    if not isinstance(text, str):
        return str(text)
    replacements = {
        "\u2014": "--",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2192": "->",
        "\u2190": "<-",
        "\u2022": "*",
        "\u00b7": "*",
        "\u2713": "[Y]",
        "\u2714": "[Y]",
        "\u2717": "[X]",
        "\u2718": "[X]",
        "\u20b9": "Rs.",
        "\u2265": ">=",
        "\u2264": "<=",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _gradient_rect(pdf, x, y, w, h, rgb_top, rgb_bottom, steps=32):
    """Approximate vertical gradient (fpdf has no native gradients)."""
    if h <= 0 or steps < 2:
        return
    r1, g1, b1 = rgb_top
    r2, g2, b2 = rgb_bottom
    band = h / steps
    for i in range(steps):
        t = i / (steps - 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        pdf.set_fill_color(r, g, b)
        pdf.rect(x, y + i * band, w, band + 0.2, "F")


def _estimate_lines(pdf, text, width_pt: float, _line_height_pt: float) -> int:
    """Rough wrapped-line count for multi_cell width (Helvetica word wrap)."""
    s = _safe(text or "")
    if not s.strip():
        return 1
    words = s.replace("\n", " \n ").split()
    lines, cur = 1, 0.0
    for w in words:
        if w == "\n":
            lines += 1
            cur = 0.0
            continue
        tw = pdf.get_string_width(w + " ")
        if cur + tw > width_pt and cur > 0:
            lines += 1
            cur = tw
        else:
            cur += tw
    return max(1, lines)


def _rounded_box(pdf, x, y, w, h, fill_rgb, draw_rgb, radius=2.5, style="FD"):
    """fpdf2 uses rect(round_corners=True), not rounded_rect()."""
    pdf.set_fill_color(*fill_rgb)
    pdf.set_draw_color(*draw_rgb)
    pdf.rect(x, y, w, h, style=style, round_corners=True, corner_radius=radius)


class TenderLensReport(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*_COLOR_NAVY)
        self.rect(0, 0, 210, 14, "F")
        self.set_xy(10, 4)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(255, 255, 255)
        self.cell(120, 6, _safe("TenderLens"), align="L")
        self.set_font("Helvetica", "", 8)
        self.cell(80, 6, _safe("Eligibility report"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*_COLOR_TEXT)
        self.set_draw_color(*_COLOR_BORDER)
        self.set_line_width(0.2)
        self.line(10, 14, 200, 14)
        self.set_y(18)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*_COLOR_BORDER)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(1)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*_COLOR_MUTED)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(*_COLOR_TEXT)

    def chapter_title(self, title):
        y0 = self.get_y()
        self.set_fill_color(*_COLOR_NAVY_LIGHT)
        self.rect(10, y0, 3.2, 9, "F")
        self.set_xy(16, y0 + 0.5)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*_COLOR_NAVY)
        self.cell(0, 8, _safe(title), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*_COLOR_BORDER)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)
        self.set_text_color(*_COLOR_TEXT)

    def section_title(self, title):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_COLOR_TEXT)
        self.cell(0, 7, _safe(title), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*_COLOR_TEXT)
        self.multi_cell(0, 5, _safe(text))
        self.ln(2)

    @staticmethod
    def verdict_badge(verdict):
        if verdict == VERDICT_ELIGIBLE:
            return "ELIGIBLE"
        if verdict == VERDICT_NOT_ELIGIBLE:
            return "NOT ELIGIBLE"
        return "NEEDS REVIEW"

    def _verdict_row_style(self, verdict):
        if verdict == VERDICT_ELIGIBLE:
            return _COLOR_PASS_BG, _COLOR_PASS_ACCENT
        if verdict == VERDICT_NOT_ELIGIBLE:
            return _COLOR_FAIL_BG, _COLOR_FAIL_ACCENT
        return _COLOR_REVIEW_BG, _COLOR_REVIEW_ACCENT

    def detail_criterion(self, verdict, code, desc, explanation):
        """One criterion block with tinted background and accent strip (like web cards)."""
        desc_s = _safe((desc or "")[:500])
        expl = _safe((explanation or "No explanation.")[:2000])
        badge = self.verdict_badge(verdict)
        bg, accent = self._verdict_row_style(verdict)

        y0 = self.get_y()
        if y0 > 250:
            self.add_page()
            y0 = self.get_y()

        line_h = 4.8
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_COLOR_TEXT)
        # measure height for bar
        h_title = self.get_string_width(f"{code}  {badge}  ") / 190 * line_h + line_h
        self.set_font("Helvetica", "", 9)
        n_lines = max(1, len(desc_s) // 95 + desc_s.count("\n"))
        n_ex = max(1, len(expl) // 95 + expl.count("\n"))
        block_h = max(h_title + n_lines * line_h + 2 + n_ex * line_h + 4, 18)

        self.set_fill_color(*accent)
        self.rect(10, y0, 1.2, block_h, "F")
        _rounded_box(self, 11, y0, 189, block_h, bg, _COLOR_BORDER, radius=2.8)

        self.set_xy(14, y0 + 2)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_COLOR_TEXT)
        self.cell(0, line_h, _safe(f"{code}  [{badge}]"), new_x="LMARGIN", new_y="NEXT")
        self.set_x(14)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(51, 65, 85)
        self.multi_cell(181, line_h, desc_s)

        self.set_x(14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_COLOR_MUTED)
        self.multi_cell(181, 4.2, expl)

        self.set_y(y0 + block_h + 3)
        self.set_text_color(*_COLOR_TEXT)


def _compute_overall(verdicts):
    mandatory = [v for v in verdicts if v.get("mandatory", True)]
    if any(v["verdict"] == VERDICT_NOT_ELIGIBLE for v in mandatory):
        return VERDICT_NOT_ELIGIBLE
    if any(v["verdict"] == VERDICT_NEEDS_REVIEW for v in mandatory):
        return VERDICT_NEEDS_REVIEW
    return VERDICT_ELIGIBLE


def generate_pdf_report(
    tender,
    criteria,
    bidders,
    all_verdicts,
    overrides=None,
) -> bytes:
    """Generate a PDF evaluation report and return it as bytes."""
    pdf = TenderLensReport()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)

    mandatory_n = sum(1 for c in criteria if c.get("mandatory"))

    # ── Cover Page ──
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    pdf.set_font("Helvetica", "B", 17)
    name_lines = _estimate_lines(pdf, tender["name"], 182, 7.5)
    name_block_h = name_lines * 7.5
    y_name = 22
    y_gen = y_name + name_block_h + 1
    y_chips = y_gen + 6
    h_chip = 24
    hero_h = max(76, y_chips + h_chip + 10)

    _gradient_rect(
        pdf,
        0,
        0,
        210,
        hero_h,
        _COLOR_NAVY,
        _COLOR_NAVY_LIGHT,
        steps=36,
    )
    pdf.set_fill_color(*_COLOR_HERO_MID)
    pdf.rect(0, hero_h - 2.5, 210, 2.5, "F")

    pdf.set_xy(14, 14)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(203, 213, 245)
    pdf.cell(
        0,
        4,
        _safe("TENDERLENS"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(165, 180, 252)
    pdf.cell(0, 4, _safe("Eligibility report"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(14, y_name)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(255, 255, 255)
    pdf.multi_cell(182, 7.5, _safe(tender["name"]))

    gen_line = datetime.now().strftime("%d %B %Y, %H:%M")
    pdf.set_xy(14, y_gen)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(226, 232, 240)
    pdf.cell(0, 5, _safe(f"Generated {gen_line}"), new_x="LMARGIN", new_y="NEXT")

    # KPI chips (white cards on hero — matches web Step 4 hero)
    kpis = [
        ("Criteria", str(len(criteria))),
        ("Mandatory", str(mandatory_n)),
        ("Bidders", str(len(bidders))),
        ("Assessments", str(len(all_verdicts))),
    ]
    bx, gap, bw = 14, 3.5, 43
    for i, (lab, val) in enumerate(kpis):
        x = bx + i * (bw + gap)
        _rounded_box(
            pdf, x, y_chips, bw, h_chip, _COLOR_CHIP_ON_HERO, (230, 236, 245), radius=3.2
        )
        pdf.set_xy(x + 5, y_chips + 3)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*_COLOR_NAVY)
        pdf.cell(bw - 10, 8, _safe(val), new_x="LMARGIN", new_y="NEXT")
        pdf.set_xy(x + 5, y_chips + 13)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(bw - 10, 4, _safe(lab.upper()))

    pdf.set_y(hero_h + 12)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*_COLOR_MUTED)
    pdf.multi_cell(
        0,
        5,
        _safe(
            "TenderLens assists with structured eligibility review. "
            "Final decisions remain with the procuring authority."
        ),
    )

    # ── Criteria Summary ──
    pdf.add_page()
    pdf.chapter_title("1. Eligibility criteria")

    for c in criteria:
        mand = "Mandatory" if c.get("mandatory") else "Optional"
        pdf.section_title(f"{c['criterion_id']} ({c['category']}, {mand})")
        pdf.body_text(c["description"])
        if c.get("threshold"):
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*_COLOR_MUTED)
            pdf.multi_cell(0, 5, _safe(f"Threshold: {c['threshold']}"))
            pdf.ln(1)
            pdf.set_text_color(*_COLOR_TEXT)

    # ── Consolidated Results ──
    pdf.add_page()
    pdf.chapter_title("2. Summary by organisation")

    bidder_names = list(dict.fromkeys(v["bidder_name"] for v in all_verdicts))

    col_org = 74
    col_n = 24
    col_o = 44
    row_h = 7

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*_COLOR_NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(col_org, row_h, _safe("Organisation"), border=1, fill=True)
    pdf.cell(col_n, row_h, _safe("Passed"), border=1, align="C", fill=True)
    pdf.cell(col_n, row_h, _safe("Not met"), border=1, align="C", fill=True)
    pdf.cell(col_n, row_h, _safe("Review"), border=1, align="C", fill=True)
    pdf.cell(col_o, row_h, _safe("Outcome"), border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_COLOR_TEXT)
    for idx, bname in enumerate(bidder_names):
        bv = [v for v in all_verdicts if v["bidder_name"] == bname]
        overall = _compute_overall(bv)
        e_count = sum(1 for v in bv if v["verdict"] == VERDICT_ELIGIBLE)
        f_count = sum(1 for v in bv if v["verdict"] == VERDICT_NOT_ELIGIBLE)
        r_count = sum(1 for v in bv if v["verdict"] == VERDICT_NEEDS_REVIEW)

        if idx % 2 == 0:
            pdf.set_fill_color(255, 255, 255)
        else:
            pdf.set_fill_color(*_COLOR_ROW_ALT)
        name_display = bname[:42] + "..." if len(bname) > 42 else bname
        pdf.cell(col_org, row_h, _safe(name_display), border=1, fill=True)
        pdf.cell(col_n, row_h, str(e_count), border=1, align="C", fill=True)
        pdf.cell(col_n, row_h, str(f_count), border=1, align="C", fill=True)
        pdf.cell(col_n, row_h, str(r_count), border=1, align="C", fill=True)

        otxt = pdf.verdict_badge(overall).replace("_", " ")
        if overall == VERDICT_ELIGIBLE:
            pdf.set_fill_color(*_COLOR_PASS_BG)
        elif overall == VERDICT_NOT_ELIGIBLE:
            pdf.set_fill_color(*_COLOR_FAIL_BG)
        else:
            pdf.set_fill_color(*_COLOR_REVIEW_BG)
        pdf.cell(col_o, row_h, _safe(otxt), border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(255, 255, 255)

    # ── Per-Bidder Detail ──
    pdf.add_page()
    pdf.chapter_title("3. Detail by organisation")

    for bname in bidder_names:
        bv = [v for v in all_verdicts if v["bidder_name"] == bname]
        overall = _compute_overall(bv)

        if pdf.get_y() > 232:
            pdf.add_page()
        yb = pdf.get_y()
        if yb > 32:
            pdf.ln(2)
            yb = pdf.get_y()
        banner_h = 11
        _rounded_box(
            pdf,
            10,
            yb,
            190,
            banner_h,
            (241, 245, 249),
            _COLOR_BORDER,
            radius=2.5,
        )
        pdf.set_xy(14, yb + 3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*_COLOR_NAVY)
        pdf.cell(115, 5, _safe(bname))
        pdf.set_font("Helvetica", "B", 8)
        ov = pdf.verdict_badge(overall)
        if overall == VERDICT_ELIGIBLE:
            pdf.set_text_color(*_COLOR_PASS_ACCENT)
        elif overall == VERDICT_NOT_ELIGIBLE:
            pdf.set_text_color(*_COLOR_FAIL_ACCENT)
        else:
            pdf.set_text_color(*_COLOR_REVIEW_ACCENT)
        pdf.cell(65, 5, _safe(f"Overall: {ov}"), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(yb + banner_h + 4)
        pdf.set_text_color(*_COLOR_TEXT)

        for v in bv:
            if pdf.get_y() > 245:
                pdf.add_page()
            pdf.detail_criterion(
                v["verdict"],
                v.get("crit_code", ""),
                v.get("crit_desc", ""),
                v.get("explanation", ""),
            )

        pdf.ln(3)

    # ── Officer Overrides ──
    if overrides:
        pdf.add_page()
        pdf.chapter_title("4. Officer overrides")
        for o in overrides:
            pdf.section_title(f"{o.get('bidder_name', '')} / {o.get('crit_code', '')}")
            pdf.body_text(
                f"Original: {o['original_verdict']} -> New: {o['new_verdict']}\n"
                f"Reason: {o['reason']}\n"
                f"Officer: {o.get('officer_name', 'N/A')} -- {o['created_at']}"
            )

    return bytes(pdf.output())

