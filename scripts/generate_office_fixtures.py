"""Generate public synthetic Office samples; never read user documents."""

import argparse
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/materials"
book = Workbook()
sheet = book.active
sheet.title = "实验数据"
sheet.append(["Name", "Value", "Date", "Formula"])
sheet.append(["Alpha uses Beta.", 42, datetime(2026, 1, 2), "=B2*2"])
hidden = book.create_sheet("Hidden notes")
hidden.sheet_state = "hidden"
hidden["A1"] = "Hidden source evidence."
book.save(ROOT / "workbook.xlsx")

deck = Presentation()
slide = deck.slides.add_slide(deck.slide_layouts[5])
slide.shapes.title.text = "研究标题"
box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
box.text_frame.text = "Alpha uses Beta."
table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1)).table
for row, values in enumerate([["Name", "Evidence"], ["Cell", "Observed"]]):
    for col, value in enumerate(values):
        table.cell(row, col).text = value
slide.notes_slide.notes_text_frame.text = "Presenter evidence."
slide.shapes.add_picture(str(ROOT / "scanned-bilingual.png"), Inches(1), Inches(4))
deck.save(ROOT / "slides.pptx")
print("Generated synthetic workbook and presentation.")

arguments = argparse.ArgumentParser()
arguments.add_argument("--legacy", action="store_true")
if arguments.parse_args().legacy:
    executable = shutil.which("soffice")
    if executable is None:
        raise RuntimeError("LibreOffice is required to generate legacy fixtures")
    with tempfile.TemporaryDirectory() as temporary:
        for source, target in [
            ("headings-table.docx", "doc"),
            ("headings-table.docx", "rtf"),
            ("headings-table.docx", "odt"),
            ("workbook.xlsx", "xls"),
            ("workbook.xlsx", "ods"),
            ("slides.pptx", "ppt"),
            ("slides.pptx", "odp"),
        ]:
            subprocess.run(
                [
                    executable,
                    "-env:UserInstallation=" + Path(temporary).as_uri(),
                    "--headless",
                    "--convert-to",
                    target,
                    "--outdir",
                    str(ROOT),
                    str(ROOT / source),
                ],
                check=True,
                timeout=45,
            )
