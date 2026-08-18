"""
fetch_pdfs.py

Downloads the minutes PDF (and agenda PDF, useful context even without a
vote breakdown) for every meeting in the DB that doesn't have a local copy
yet. Run after fetch_meetings.py.
"""

import time
from pathlib import Path

import requests

from db import get_conn

PDF_DIR = Path(__file__).parent / "data" / "pdfs"
USER_AGENT = "waltham-council-tracker/0.1 (personal project; contact: YOUR_EMAIL_HERE)"


def _download(session: requests.Session, url: str, dest: Path) -> bool:
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF-"):
        # CivicPlus returns a 200 HTML page (e.g. a 404) for some broken/removed
        # links instead of a real 404 status -- skip rather than save garbage.
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return True


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, doc_id, minutes_url, agenda_url, minutes_pdf_path, agenda_pdf_path
        FROM meetings
        WHERE minutes_pdf_path IS NULL OR (agenda_pdf_path IS NULL AND agenda_url IS NOT NULL)
        """
    ).fetchall()

    session = requests.Session()
    downloaded, skipped = 0, 0
    for row in rows:
        if row["minutes_url"] and not row["minutes_pdf_path"]:
            dest = PDF_DIR / f"{row['doc_id']}_minutes.pdf"
            if _download(session, row["minutes_url"], dest):
                conn.execute(
                    "UPDATE meetings SET minutes_pdf_path = ? WHERE id = ?",
                    (str(dest), row["id"]),
                )
                downloaded += 1
            else:
                skipped += 1
            time.sleep(0.5)  # polite pacing

        if row["agenda_url"] and not row["agenda_pdf_path"]:
            dest = PDF_DIR / f"{row['doc_id']}_agenda.pdf"
            if _download(session, row["agenda_url"], dest):
                conn.execute(
                    "UPDATE meetings SET agenda_pdf_path = ? WHERE id = ?",
                    (str(dest), row["id"]),
                )
                downloaded += 1
            else:
                skipped += 1
            time.sleep(0.5)

        conn.commit()

    conn.close()
    print(f"Downloaded {downloaded} PDFs, skipped {skipped} broken/unavailable links")


if __name__ == "__main__":
    main()
