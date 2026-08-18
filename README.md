# Waltham City Council Tracker

A GovTrack-style tool for the Waltham, MA City Council: scrapes meeting
minutes, extracts roll-call votes and behavioral signals (sponsorship,
committee activity, recusals/absences, floor remarks), and provides topic
search, councilor profiles, and a councilor similarity map.

## Setup

```
py -3 -m pip install -r requirements.txt
py -3 seed_councilors.py
py -3 fetch_meetings.py
py -3 fetch_pdfs.py
py -3 extract_text.py
py -3 parse_minutes.py
py -3 -m streamlit run app.py
```

`seed_councilors.py` only needs to be re-run after an election or a
mid-term appointment changes the roster (edit the `ROSTER` list in that
file). The other four steps can just be re-run any time to pick up new
meetings -- each is idempotent (upserts by AgendaCenter doc id), or use the
"Refresh data" button in the app sidebar, which runs all four in sequence.

**Optional: OCR for scanned-image minutes.** A handful of minutes PDFs are
scanned images with no text layer at all rather than the usual born-digital
PDF (confirmed real minutes, not blank/cancelled meetings). `extract_text.py`
falls back to OCR for those, but that requires the [Tesseract OCR
engine](https://github.com/UB-Mannheim/tesseract/wiki) installed separately
-- it's a compiled binary, not something `pip install` can pull in. On
Windows: `winget install --id UB-Mannheim.TesseractOCR -e`. Without it,
`extract_text.py` still runs fine and just prints a warning; those specific
meetings will stay unparsed until Tesseract is installed and it's re-run.

## What's covered

- **2025-present** meetings from the CivicPlus AgendaCenter
  (`/AgendaCenter/City-Council-2/`). Pre-2025 minutes live in a separate,
  JS-driven Document Center and are **not** covered yet -- see Known
  limitations.
- Roll-call votes, parsed into individual yes/no/recused/absent/presiding
  records per councilor.
- Voice-vote items (the majority) are recorded as items but without a
  per-member breakdown, since none is in the source -- that's the real
  format, not a parsing gap.
- Behavioral signals: who moved/sponsored an item, who spoke or asked
  questions on it (tied to the nearest agenda item), attendance, and
  recusals.
- Full-text keyword search over agenda item descriptions (SQLite FTS5).

## The "ideology" chart

Real 2025-2026 data shows Waltham's council is overwhelmingly
consensus-driven: of 96 roll-call votes captured so far, only 11 had any
dissenting vote at all. A pure yes/no vote-agreement score (the classic
GovTrack/DW-NOMINATE approach) would barely distinguish most councilors.
The Similarity Map in the app therefore combines roll-call agreement with
a behavioral profile (which committees a councilor sponsors items in,
speaks up in, and their recusal/absence pattern) -- it's framed as a
**similarity map**, not a left-right ideology axis, since that's what the
signal actually measures here.

## Known limitations

- **Pre-2025 archive not implemented.** The Document Center
  (`/DocumentCenter/Index/868`) renders its file listing via JavaScript, so
  it didn't expose document links to a plain HTTP fetch. Whether those
  older PDFs even have a text layer (vs. being scanned images needing OCR)
  is unverified. Extending `fetch_meetings.py` to cover this is a
  reasonable phase 2 -- likely needs a browser-automation fetch for the
  listing, then the same text-layer check `extract_text.py` already does.
- **Two different minutes formats are handled.** Most minutes are the full
  "Waltham City Council" format (`parse_meeting()`). A second, shorter
  format is used for standalone "Committee of the Whole" sessions
  (`parse_committee_of_whole_meeting()`) -- different header, different
  attendance phrasing, and roll-call votes written inline in a sentence
  ("...on a roll call vote of 11 in favor (...), 1 absent (...) ... and
  LaCava presiding") rather than the main format's labeled block. This was
  only discovered because 6 minutes PDFs needed OCR (see below) and turned
  out, on manual review, to be this second format -- which also revealed 6
  *more* meetings already had normal PDF text but were being silently
  mis-parsed by the main-format parser before this fix. If a future
  meeting shows up with yet another format variant, expect similarly
  wrong-not-missing results until it's specifically handled -- this parser
  only knows the formats it's been shown.
- **6 of 67 fetched minutes PDFs are scanned images with no text layer**,
  confirmed as real minutes (not cancellation notices) by manual review.
  `extract_text.py` OCRs these via Tesseract (see Setup) -- without
  Tesseract installed, they stay unparsed rather than crashing anything.
- **The parser is rule-based, not a formal grammar**, built against real
  minutes but covering specific narrative styles (two, now). It's
  rigorously checked against actual roll-call blocks (validated against
  four full source documents by hand across both formats, matching 100%
  including every "Absent"/"Recused" ordering quirk and PDF-extraction
  artifact found). Item/committee/disposition tagging and floor-remark
  attribution are best-effort and can occasionally mis-bucket an item in
  an unusual meeting; the FTS search matches on the stored description
  text, so a mis-tagged section doesn't hide an item from search, it just
  files it oddly.
- **PDF text extraction artifacts**: pypdf occasionally inserts a spurious
  space mid-word (e.g. "requested" -> "re quested") and some meetings use a
  Wingdings-style Private-Use-Area bullet glyph instead of a normal
  bullet -- both are handled in `parse_minutes.py`, but a future PDF
  template change could introduce a new variant.
- "Public statements" only covers what's recorded in the minutes
  themselves (e.g. "Councillor X spoke on the matter"). It does not cover
  news coverage, press releases, or social media.

## Files

| File | Purpose |
|---|---|
| `db.py` | SQLite schema + connection helper |
| `seed_councilors.py` | Hand-maintained current + former councilor roster |
| `fetch_meetings.py` | Scrapes the AgendaCenter meeting list into `meetings` |
| `fetch_pdfs.py` | Downloads minutes/agenda PDFs |
| `extract_text.py` | pypdf text extraction into `meetings.raw_text` |
| `parse_minutes.py` | Core parser: votes, attendance, agenda items, remarks |
| `build_similarity.py` | Vote-agreement + behavioral similarity/MDS computation |
| `app.py` | Streamlit interactive app |
| `qa.py` | Offline keyword Q&A backing the app's Chat page (no LLM, no tokens) |
| `tests/` | pytest suite -- see CONTRIBUTING.md |

## Contributing

Bug reports and feature ideas are welcome — see `CONTRIBUTING.md` for how
to run it locally and what's useful to include in an issue.
