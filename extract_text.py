"""
extract_text.py

Runs pypdf text extraction over every downloaded minutes PDF and stores the
raw text on the meeting row, for parse_minutes.py to work from. Only minutes
PDFs are extracted -- agendas are kept on disk for reference but don't carry
vote data.
"""

from datetime import datetime, timezone

from pypdf import PdfReader

from db import get_conn


def extract_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def main() -> None:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, minutes_pdf_path FROM meetings
        WHERE minutes_pdf_path IS NOT NULL AND raw_text IS NULL
        """
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    n_ok, n_fail = 0, 0
    for row in rows:
        try:
            text = extract_pdf_text(row["minutes_pdf_path"])
        except Exception as exc:  # pypdf can choke on malformed PDFs
            print(f"  FAILED {row['minutes_pdf_path']}: {exc}")
            n_fail += 1
            continue
        conn.execute(
            "UPDATE meetings SET raw_text = ?, scraped_at = ? WHERE id = ?",
            (text, now, row["id"]),
        )
        n_ok += 1
    conn.commit()
    conn.close()
    print(f"Extracted text from {n_ok} PDFs ({n_fail} failed)")


if __name__ == "__main__":
    main()
