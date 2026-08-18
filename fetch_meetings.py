"""
fetch_meetings.py

Scrapes the Waltham CivicPlus AgendaCenter for City Council meetings
(covers 2025-present; the pre-2025 archive lives in a separate,
JS-driven Document Center and is out of scope for now).

The AgendaCenter's default page renders the current year's rows as plain
server-side HTML. Older years are fetched via the same AJAX endpoint the
page's own "year" tabs call (POST /AgendaCenter/UpdateCategoryList with
{year, catID}), which returns the identical row markup as an HTML
fragment -- catID=2 is "City Council" (matches the "City-Council-2" path
segment on the site itself).

Each row looks like:
    <tr class="catAgendaRow">
      <td>
        <h3><strong aria-label="Agenda for August 3, 2026">Aug 3, 2026</strong></h3>
        <p><a id="08032026-642" href="/AgendaCenter/ViewFile/Agenda/_08032026-642">
             Summer Waltham City Council Meeting</a></p>
      </td>
      <td class="minutes"><a href="/AgendaCenter/ViewFile/Minutes/_08032026-642">...</a></td>
      ...
    </tr>
"""

import argparse
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from db import get_conn, init_db

BASE = "https://www.city.waltham.ma.us"
LIST_URL = f"{BASE}/AgendaCenter/City-Council-2/"
YEAR_URL = f"{BASE}/AgendaCenter/UpdateCategoryList"
CITY_COUNCIL_CAT_ID = 2
USER_AGENT = "waltham-council-tracker/0.1 (personal project; contact: 134563766+HoyaDraeg@users.noreply.github.com)"

DATE_RE = re.compile(r"^\d{8}-\d+$")


def _parse_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    meetings = []
    for row in soup.find_all("tr", class_="catAgendaRow"):
        strong = row.find("strong", attrs={"aria-label": re.compile(r"^Agenda for ")})
        title_td = row.find("td")
        link = title_td.find("a", id=DATE_RE) if title_td else None
        if not strong or not link:
            continue

        date_text = strong["aria-label"].removeprefix("Agenda for ").strip()
        meeting_date = datetime.strptime(date_text, "%B %d, %Y").date().isoformat()

        doc_id = link["id"]
        agenda_url = BASE + link["href"] if link.get("href", "").startswith("/") else link.get("href")
        title = link.get_text(strip=True)

        minutes_url = None
        minutes_td = row.find("td", class_="minutes")
        if minutes_td:
            m_link = minutes_td.find("a")
            if m_link and m_link.get("href"):
                minutes_url = BASE + m_link["href"] if m_link["href"].startswith("/") else m_link["href"]

        meetings.append({
            "doc_id": doc_id,
            "meeting_date": meeting_date,
            "title": title,
            "agenda_url": agenda_url,
            "minutes_url": minutes_url,
        })
    return meetings


def fetch_year(session: requests.Session, year: int) -> list[dict]:
    resp = session.post(
        YEAR_URL,
        data={"year": year, "catID": CITY_COUNCIL_CAT_ID},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    return _parse_rows(resp.text)


def upsert_meetings(meetings: list[dict]) -> int:
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for m in meetings:
        cur = conn.execute(
            """
            INSERT INTO meetings (doc_id, meeting_date, title, agenda_url, minutes_url, scraped_at)
            VALUES (:doc_id, :meeting_date, :title, :agenda_url, :minutes_url, :scraped_at)
            ON CONFLICT(doc_id) DO UPDATE SET
                meeting_date = excluded.meeting_date,
                title = excluded.title,
                agenda_url = excluded.agenda_url,
                minutes_url = excluded.minutes_url,
                scraped_at = excluded.scraped_at
            """,
            {**m, "scraped_at": now},
        )
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Waltham City Council meeting list")
    parser.add_argument("--from-year", type=int, default=2025)
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    args = parser.parse_args()

    init_db()
    session = requests.Session()
    all_meetings = []
    for year in range(args.from_year, args.to_year + 1):
        year_meetings = fetch_year(session, year)
        print(f"{year}: found {len(year_meetings)} meetings")
        all_meetings.extend(year_meetings)

    n = upsert_meetings(all_meetings)
    with_minutes = sum(1 for m in all_meetings if m["minutes_url"])
    print(f"Upserted {n} meeting rows ({len(all_meetings)} total, {with_minutes} with minutes available)")


if __name__ == "__main__":
    main()
