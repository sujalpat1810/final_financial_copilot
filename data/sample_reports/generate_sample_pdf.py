"""
Helper script to convert the sample .txt report into a real PDF for testing.
Run once: python data/sample_reports/generate_sample_pdf.py

Requires: pip install reportlab
The generated PDF is saved alongside this script.
"""

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def txt_to_pdf(txt_path: Path, pdf_path: Path) -> None:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        print("reportlab not installed — run: pip install reportlab")
        sys.exit(1)

    text = txt_path.read_text(encoding="utf-8")
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
        elif stripped.isupper() and len(stripped) > 5:
            story.append(Paragraph(stripped, styles["Heading2"]))
        else:
            story.append(Paragraph(stripped, styles["Normal"]))

    doc.build(story)
    print(f"Created {pdf_path}")


if __name__ == "__main__":
    src = SCRIPT_DIR / "acme_annual_report_2023.txt"
    dst = SCRIPT_DIR / "acme_annual_report_2023.pdf"
    txt_to_pdf(src, dst)
