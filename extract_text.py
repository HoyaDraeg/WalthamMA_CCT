"""
extract_text.py

Runs pypdf text extraction over every downloaded minutes PDF and stores the
raw text on the meeting row, for parse_minutes.py to work from. Only
minutes PDFs are extracted -- agendas are kept on disk for reference but
don't carry vote data.

Falls back to OCR for any page pypdf gets zero text from. This matters in
practice, not just as an edge case: 6 of the first 61 minutes PDFs fetched
turned out to be scanned images with no text layer at all, and manual
review confirmed they're real minutes (not cancellation notices), so
without OCR those meetings' votes are silently missing from the dataset.

OCR uses PyMuPDF (`fitz`) to rasterize just the empty pages -- chosen over
pdf2image because pdf2image needs the poppler binary, which this project
already doesn't otherwise depend on -- piped through the Tesseract OCR
engine via pytesseract. Tesseract itself is a compiled binary, not
pip-installable; see README for setup. If it's not found on this machine,
OCR is skipped with a warning (not a crash) so this script still runs for
anyone who hasn't installed it, they'll just keep the same gaps documented
in the README until they do.
"""

import io
import os
import shutil
from datetime import datetime, timezone

import pymupdf as fitz
from PIL import Image
from pypdf import PdfReader

from db import get_conn

# winget's default install location on Windows, in case it's not on PATH
# yet in the current shell session (a fresh terminal usually does have it).
TESSERACT_CANDIDATES = [
    "tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
]


def find_tesseract() -> str | None:
    for candidate in TESSERACT_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    for candidate in TESSERACT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def ocr_page(doc: fitz.Document, page_index: int, tesseract_cmd: str) -> str:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    pix = doc[page_index].get_pixmap(dpi=300)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def extract_pdf_text(path: str, tesseract_cmd: str | None) -> tuple[str, bool]:
    """Returns (text, used_ocr)."""
    reader = PdfReader(path)
    pages_text = [page.extract_text() or "" for page in reader.pages]
    used_ocr = False

    if tesseract_cmd and any(not t.strip() for t in pages_text):
        doc = fitz.open(path)
        for i, text in enumerate(pages_text):
            if text.strip():
                continue
            ocr_text = ocr_page(doc, i, tesseract_cmd)
            if ocr_text.strip():
                pages_text[i] = ocr_text
                used_ocr = True
        doc.close()

    return "\n".join(pages_text), used_ocr


def main() -> None:
    conn = get_conn()
    tesseract_cmd = find_tesseract()
    if tesseract_cmd:
        print(f"OCR fallback enabled (tesseract: {tesseract_cmd})")
    else:
        print("Tesseract not found on this machine -- OCR fallback disabled. "
              "Scanned-image minutes will stay empty. See README for setup.")

    # raw_text = '' (not just NULL) means a prior run got nothing from a
    # page -- re-check those too now that OCR may be available, instead of
    # only picking up meetings that have never been processed at all.
    rows = conn.execute(
        """
        SELECT id, doc_id, minutes_pdf_path FROM meetings
        WHERE minutes_pdf_path IS NOT NULL AND (raw_text IS NULL OR raw_text = '')
        """
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    n_ok, n_fail, n_ocr = 0, 0, 0
    for row in rows:
        try:
            text, used_ocr = extract_pdf_text(row["minutes_pdf_path"], tesseract_cmd)
        except Exception as exc:  # pypdf/fitz can choke on malformed PDFs
            print(f"  FAILED {row['minutes_pdf_path']}: {exc}")
            n_fail += 1
            continue
        conn.execute(
            "UPDATE meetings SET raw_text = ?, scraped_at = ? WHERE id = ?",
            (text, now, row["id"]),
        )
        n_ok += 1
        if used_ocr:
            n_ocr += 1
            print(f"  OCR'd {row['doc_id']} ({len(text)} chars)")
    conn.commit()
    conn.close()
    print(f"Extracted text from {n_ok} PDFs ({n_ocr} needed OCR, {n_fail} failed)")


if __name__ == "__main__":
    main()
