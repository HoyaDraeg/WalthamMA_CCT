# Contributing

Thanks for testing this out. This started as a personal project, so please
be patient with rough edges — feedback and PRs are genuinely welcome.

## Running it locally

```
py -3 -m pip install -r requirements.txt
py -3 seed_councilors.py
py -3 fetch_meetings.py
py -3 fetch_pdfs.py
py -3 extract_text.py
py -3 parse_minutes.py
py -3 -m streamlit run app.py
```

See `README.md` for what each step does and what's covered so far
(2025-present meetings; the pre-2025 archive isn't wired up yet).

## Running the tests

```
py -3 -m pip install -r requirements-dev.txt
py -3 -m pytest -v
```

The suite doesn't touch the real scraped database — `tests/conftest.py`
points every test at a fresh temp SQLite file seeded with a small,
hand-traceable fixture (5 councilors, 2 meetings, documented in that
file's docstring), so it's fast and doesn't depend on network access or
whatever's currently in your `data/`. `tests/test_parse_minutes.py` is the
one to read first if you're touching the parser — most of its cases exist
because of a real PDF-extraction artifact that silently dropped or
corrupted data at some point (a page number landing mid-sentence, a
councilor's name swallowed into an unrelated sentence, etc.), so it
doubles as a list of "gotchas" for this kind of text.

**Please run the suite before opening a PR**, and add a case to it if
you're fixing a parsing bug — a regression test from the actual minutes
text that exposed the bug is far more valuable than the fix alone, since
it's what stops the same bug from quietly coming back.

## Reporting a bug or a bad parse

The parser (`parse_minutes.py`) is rule-based against real Waltham minutes,
not a formal grammar, so it can occasionally mis-file an item or miss a
vote on an unusual meeting. If you spot one, a **bug report** issue with
the meeting date and what looked wrong is the most useful thing you can
give — it's usually a quick regex fix once there's a concrete example.

## Suggesting a feature

Open a **feature request** issue. If it's about the similarity/ideology
chart specifically, it helps to say what you were expecting to see and
why the current view didn't get you there — the "similarity vs. ideology"
framing in the README was itself a response to that kind of feedback.

## Making a change

1. Fork the repo, branch off `main`.
2. Keep changes scoped — small, focused PRs are much easier to review than
   one that touches the scraper, the parser, and the app at once.
3. If you touch `parse_minutes.py`, please note which real meeting(s) you
   validated the change against (date + doc id is enough) — the roll-call
   extraction in particular has been hand-checked against actual source
   PDFs and it's easy to fix one meeting's formatting quirk while breaking
   another's.
4. Open a PR describing what changed and why.

## Project layout

See the table at the bottom of `README.md` for what each file does.
