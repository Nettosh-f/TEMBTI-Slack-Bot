import pymupdf
from io import BytesIO
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

def pdf_to_images_pymupdf(pdf_bytes):
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(BytesIO(pix.tobytes("png")))
        images.append(img)
    return images


def slack_format(text):
    out = []
    for line in text.splitlines():
        if line.strip().startswith("-") or line.strip().startswith("•"):
            out.append(f"> {line}")
        elif line.strip() == "":
            out.append(">")
        elif line.endswith(":"):
            out.append(f"*{line}*")
        else:
            out.append(f"> {line}")
    return "\n".join(out)


def create_pdf_from_text(text):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    lines = text.split('\n')
    y = height - 72  # 1 inch from top
    for line in lines:
        c.drawString(72, y, line)
        y -= 14
        if y < 72:
            c.showPage()
            y = height - 72
    c.save()
    buf.seek(0)
    return buf