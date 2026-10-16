"""Create only tiny public synthetic parser fixtures; no models or OCR."""

import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "materials"
ROOT.mkdir(parents=True, exist_ok=True)

writer = PdfWriter()
font = writer._add_object(
    DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
)
for text in ["First page evidence.", "Second page evidence."]:
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
writer.write(ROOT / "native.pdf")
writer.encrypt("fixture-password")
writer.write(ROOT / "encrypted.pdf")

image = Image.new("RGB", (400, 100), "white")
font_path = Path("/System/Library/Fonts/PingFang.ttc")
font_image = (
    ImageFont.truetype(str(font_path), 20) if font_path.exists() else ImageFont.load_default()
)
ImageDraw.Draw(image).text((10, 12), "Scanned evidence / 扫描证据", fill="black", font=font_image)
image.save(ROOT / "scanned-bilingual.png")
writer = PdfWriter()
page = writer.add_blank_page(width=400, height=100)
bitmap = DecodedStreamObject()
bitmap.set_data(zlib.compress(image.tobytes()))
bitmap.update(
    {
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(400),
        NameObject("/Height"): NumberObject(100),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
        NameObject("/Filter"): NameObject("/FlateDecode"),
    }
)
reference = writer._add_object(bitmap)
page[NameObject("/Resources")] = DictionaryObject(
    {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): reference})}
)
stream = DecodedStreamObject()
stream.set_data(b"q 400 0 0 100 0 0 cm /Im0 Do Q")
page[NameObject("/Contents")] = writer._add_object(stream)
writer.write(ROOT / "scanned-bilingual.pdf")

with ZipFile(ROOT / "headings-table.docx", "w", ZIP_DEFLATED) as archive:
    archive.writestr(
        "[Content_Types].xml",
        """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override
PartName="/word/document.xml"
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
    )
    archive.writestr(
        "_rels/.rels",
        """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
Target="word/document.xml"/></Relationships>""",
    )
    archive.writestr(
        "word/document.xml",
        """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p>
<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
<w:r><w:t>Research heading</w:t></w:r></w:p>
<w:p>
<w:r><w:t xml:space="preserve">  细胞 evidence paragraph.</w:t></w:r></w:p>
<w:tbl>
<w:tr>
<w:tc>
<w:p>
<w:r><w:t>Name</w:t></w:r></w:p></w:tc>
<w:tc>
<w:p>
<w:r><w:t>Evidence</w:t></w:r></w:p></w:tc></w:tr>
<w:tr>
<w:tc>
<w:p>
<w:r><w:t>Cell</w:t></w:r></w:p></w:tc>
<w:tc>
<w:p>
<w:r><w:t>Observed</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>""",
    )
print("Generated tiny native/encrypted/scanned PDF and DOCX fixtures.")
