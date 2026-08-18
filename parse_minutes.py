"""
parse_minutes.py

Rule-based parser over the extracted minutes text. Built directly against
real 2025-2026 Waltham City Council minutes -- see the format notes below,
all confirmed from actual documents, not guessed:

- Text layout varies (spacing, "1.The Mayor" vs "1. The Mayor"), so the
  first step is to collapse all whitespace to single spaces.
- Roll-call blocks look like:
    Roll call required. In favor: Councillors A, B, C. Opposed: D, E.
    [Absent: F.] [Recused: G.] [Presiding: H.] Approved 12-2-0-1.
  "Absent"/"Recused"/"Presiding" are all optional and their ORDER VARIES
  between meetings -- do not assume a fixed order.
- Name lists sometimes contain a stray "Councillors," artifact right after
  the label (e.g. "In favor: Councillors, Brasco, Dunn, ..." -- "Brasco" is
  the first real name). Names are therefore extracted by whitelisting
  against the known councilor last-name set, not by naive comma-splitting.
- Most items pass "by a voice vote" with no per-member breakdown -- that's
  normal, not a parse failure.
- Committee Reports has sub-headers per committee (Finance, Ordinances and
  Rules, Licenses and Franchises, Public Works and Public Safety, Economic
  and Community Development, Committee of the Whole, Debt and Capital
  Planning / "Debt & Capital Planning") immediately followed by a numbered
  list, which is how sub-headers are distinguished from ordinary prose
  mentioning a committee by name.

This is a best-effort narrative parser, not a formal grammar -- the roll
call vote extraction is the part that matters most for the vote-agreement
analysis and is the most rigorously tested; item/committee/disposition
tagging and behavioral remarks are best-effort for topic search and may
occasionally mis-bucket an item. That's an acceptable tradeoff for a
personal research tool.
"""

import re
from datetime import datetime, timezone

from db import get_conn

TOP_SECTIONS = [
    ("Communications from the Mayor", "Communications from the Mayor"),
    ("Applications and Licenses", "Applications and Licenses"),
    ("Resolutions, Orders and Ordinances", "Resolutions, Orders and Ordinances"),
    ("Committee Reports", "Committee Reports"),
    ("Unfinished Business & Other Business", "Unfinished Business & Other Business"),
    ("Unfinished Business and Other Business", "Unfinished Business & Other Business"),
    ("Tabled Items", "Tabled Items"),
]

COMMITTEES = [
    "Committee of the Whole",
    "Ordinances and Rules",
    "Finance",
    "Licenses and Franchises",
    "Public Works and Public Safety",
    "Economic and Community Development",
    "Debt and Capital Planning",
    "Debt & Capital Planning",
    "Public Facilities",
    "Health and Human Services",
]

PUBLIC_HEARING_RE = re.compile(
    r"(First|Second|Third|Fourth|Fifth|Sixth|Six|Seventh|Eighth|Ninth|Tenth)\s+(?:Joint\s+)?Public Hearing:"
)
ITEM_NUM_RE = re.compile(r"(?<=\s)\d{1,2}\.\s?(?=[A-Z][a-zA-Z])")
# \u2022 is the normal bullet; \uf0b7 is a Wingdings-style bullet glyph some
# meetings' PDFs map into the Unicode Private Use Area instead -- both
# appear in the corpus depending on which template/year produced the
# minutes.
BULLET_RE = re.compile("[\u2022\uf0b7]")

# pypdf sometimes leaves ligature glyphs un-decomposed depending on the
# embedded font, which breaks plain-text search/matching (e.g. "o\ufb03ce"
# instead of "office") -- normalize the common ones back to ASCII.
LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}

# pypdf occasionally inserts a spurious space mid-word due to font kerning
# (e.g. "requested" -> "re quested") -- tolerate an optional space after "re".
ROLL_CALL_TRIGGER_RE = re.compile(r"Roll call\s+re\s*(?:quired|quested)\s*\.\s*")
FAVOR_OPPOSED_RE = re.compile(r"In favor:\s*(.*?)\.\s*Opposed:\s*(.*?)\.\s*")
EXTRA_LABEL_RE = re.compile(r"(Absent|Recused|Presiding):\s*(.*?)\.\s*")

DISPOSITION_RULES = [
    # matches "withdraw", "withdrawn", and "withdrawal" -- all appear in
    # real minutes ("moved to withdraw...", "...was withdrawn...", "the
    # withdrawal without prejudice").
    ("withdrawn", re.compile(r"\bwithdraw", re.I)),
    ("tabled", re.compile(r"\btabled\b", re.I)),
    ("failed", re.compile(r"\b(?:failed|denied|defeated)\b", re.I)),
    ("referred", re.compile(r"[Rr]eferred to the (.+?) Committee")),
    ("filed", re.compile(r"\bfiled\b", re.I)),
    ("approved", re.compile(r"\b(?:approved|adopted)\b", re.I)),
]

REMARK_PATTERNS = [
    ("moved", re.compile(r"Councillors?\s+([A-Z][a-zA-Z\-]+)\s+move[ds]?\b")),
    ("recused", re.compile(r"Councillors?\s+([A-Z][a-zA-Z\-]+(?:\s*,\s*[A-Z][a-zA-Z\-]+)*)\s+recused")),
    ("asked_question", re.compile(r"Councillors?\s+([A-Z][a-zA-Z\-]+(?:\s*,\s*[A-Z][a-zA-Z\-]+)*(?:\s+and\s+[A-Z][a-zA-Z\-]+)?)\s+asked\b")),
    ("spoke", re.compile(r"Councillors?\s+([A-Z][a-zA-Z\-]+(?:\s*,\s*[A-Z][a-zA-Z\-]+)*(?:\s+and\s+[A-Z][a-zA-Z\-]+)?)\s+spoke\b")),
]

# ------------------------------------------------------------------
# "Committee of the Whole" standalone minutes are a second, shorter minutes
# format used for some COW-only sessions (confirmed from 6 real 2025-2026
# meetings, all of which happened to be scanned-image PDFs needing OCR).
# Different header, different attendance phrasing, and roll-call votes
# embedded inline in a sentence rather than the labeled block format the
# main "Waltham City Council" minutes use, e.g.:
#   "...adopted on a roll call vote of 11 in favor (A, B, C), 1 absent (D),
#    2 not present at the ES meeting (E, F) and LaCava presiding."
# The clause set and order both vary (not every meeting has all of
# in-favor/opposed/absent/not-present, and "presiding" is only sometimes
# present) -- parsed as a generic repeated (count, category, names) loop
# rather than a fixed template, same approach as the main format's
# Absent/Recused/Presiding handling.
COW_HEADER_RE = re.compile(r"^(?:committee of the whole)\b", re.IGNORECASE)
COW_ATTENDANCE_RE = re.compile(
    r"call(?:ed)? the roll\s*[-—]\s*(?P<present>.*?)\s+were (?:all )?present in-person\.\s*"
    r"(?:Councillors?\s+(?P<absent>.*?)\s+(?:was|were) absent\.)?",
    re.IGNORECASE,
)
COW_ROLLCALL_TRIGGER_RE = re.compile(r"roll call vote of\s+", re.IGNORECASE)
COW_ROLLCALL_CLAUSE_RE = re.compile(
    r"\d+\s+(in favor|opposed|against|absent|not present[^(]*?)\s*\(([^)]*)\)\s*,?\s*(?:and\s+)?",
    re.IGNORECASE,
)
COW_PRESIDING_RE = re.compile(r"(?:Vice-President\s+|President\s+|VP\s+)?([A-Z][a-zA-Z\-]+)\s+presiding", re.IGNORECASE)
COW_BOILERPLATE_RE = re.compile(
    r"^(committee of the whole|minutes of the meeting|\w+day\s*[-—]|clerk)|"
    r"clerk (pro tem )?to the committee of the whole",
    re.IGNORECASE,
)


PAGE_NUMBER_LINE_RE = re.compile(r"^\s*\d{1,3}\s*$")


def normalize(text: str) -> str:
    # PDF page breaks leave a bare page-number line (e.g. a lone "5") in the
    # extracted text, sometimes landing mid-sentence (between "Roll call
    # requested." and "In favor:") -- strip those before collapsing
    # whitespace, or they corrupt the regexes that expect adjacent labels.
    # Real numbered list items always keep the number and its period
    # glued to the following word on one line (e.g. "1.The Mayor"), so this
    # never strips a real item marker.
    lines = [ln for ln in text.split("\n") if not PAGE_NUMBER_LINE_RE.match(ln)]
    text = "\n".join(lines)
    for ligature, replacement in LIGATURES.items():
        text = text.replace(ligature, replacement)
    return re.sub(r"\s+", " ", text).strip()


def make_extract_names(known_last_names: set[str]):
    def extract_names(fragment: str) -> list[str]:
        if not fragment or fragment.strip().lower() in ("none", ""):
            return []
        tokens = re.split(r",| and ", fragment)
        names = []
        for t in tokens:
            t = t.strip().strip(".").strip()
            t = re.sub(r"^Councillors?\s*", "", t)
            # order matters: "Vice-President"/"VP" must be tried before the
            # plain "President" strip, or "Vice-" would be left dangling.
            t = re.sub(r"^Vice-President\s+", "", t, flags=re.IGNORECASE)
            t = re.sub(r"^VP\s+", "", t, flags=re.IGNORECASE)
            t = re.sub(r"^President\s+", "", t)
            if t in known_last_names and t not in names:
                names.append(t)
        return names
    return extract_names


PRESENT_LABEL_RE = re.compile(r"Present:\s*")
ABSENT_LABEL_RE = re.compile(r"Absent:\s*")
# Deliberately does NOT strip a "Councillor(s)" prefix like extract_names()
# does -- that prefix is how ordinary narrative sentences introduce any
# councilor's action ("Councillor Jones moved..."), so allowing it here
# would let the scanner run straight past the end of the roster line into
# the next, unrelated sentence and misread the first name it mentions as
# still being part of the roster. Only President/Vice-President/VP are
# safe to strip, since those genuinely appear inside the roster line
# itself (naming the presiding officer).
NAME_TOKEN_RE = re.compile(
    r"\s*,?\s*(?:and\s+)?(?:Vice-President\s+|VP\s+|President\s+)?([A-Z][a-zA-Z\-]+)",
    re.IGNORECASE,
)


def scan_name_list(text: str, pos: int, known_last_names: set[str]) -> tuple[list[str], int]:
    """Greedily consumes a comma/'and'-separated run of known councilor
    names starting at pos, stopping at the first token that isn't a known
    name -- rather than requiring a trailing period like a naive regex
    would. This matters because the top-level meeting roster line
    sometimes runs directly into the next sentence with no punctuation at
    all in between (a PDF-extraction artifact, e.g. "Absent: Vidal City
    Clerk Vizard recited..." with no period after "Vidal") -- a
    period-anchored capture either grabs way too much text or, since the
    swallowed blob then fails extract_names()'s exact-match check, silently
    drops the name entirely. Scanning token-by-token against the whitelist
    stops cleanly right after the last real name regardless of what
    punctuation (if any) follows.
    """
    names = []
    while True:
        m = NAME_TOKEN_RE.match(text, pos)
        if not m:
            break
        name = m.group(1)
        if name not in known_last_names:
            break
        if name not in names:
            names.append(name)
        pos = m.end()
    return names, pos


def find_roll_calls(text: str, extract_names) -> list[dict]:
    """Find every roll-call vote block; returns dicts with char span + parsed votes."""
    blocks = []
    for trigger in ROLL_CALL_TRIGGER_RE.finditer(text):
        pos = trigger.end()
        m = FAVOR_OPPOSED_RE.match(text, pos)
        if not m:
            continue
        favor_raw, opposed_raw = m.group(1), m.group(2)
        pos = m.end()
        extras: dict[str, str] = {}
        while True:
            m2 = EXTRA_LABEL_RE.match(text, pos)
            if not m2:
                break
            extras[m2.group(1).lower()] = m2.group(2)
            pos = m2.end()

        votes = {}
        for name in extract_names(favor_raw):
            votes[name] = "yes"
        for name in extract_names(opposed_raw):
            votes[name] = "no"
        for name in extract_names(extras.get("absent", "")):
            votes[name] = "absent"
        for name in extract_names(extras.get("recused", "")):
            votes[name] = "recused"
        for name in extract_names(extras.get("presiding", "")):
            votes.setdefault(name, "presiding")

        blocks.append({"start": trigger.start(), "end": pos, "votes": votes})
    return blocks


def find_boundaries(text: str) -> list[tuple[int, str, str]]:
    """Returns sorted (position, kind, label) markers: section, committee, hearing, item."""
    boundaries = []
    for phrase, canonical in TOP_SECTIONS:
        for m in re.finditer(re.escape(phrase), text):
            boundaries.append((m.start(), "section", canonical))

    committee_alt = "|".join(re.escape(c) for c in COMMITTEES)
    committee_re = re.compile(rf"\b(?:{committee_alt})\b(?=\s*\d{{1,2}}\.\s?[A-Z])")
    for m in committee_re.finditer(text):
        name = m.group(0)
        if name == "Debt & Capital Planning":
            name = "Debt and Capital Planning"
        boundaries.append((m.start(), "committee", name))

    for m in PUBLIC_HEARING_RE.finditer(text):
        boundaries.append((m.start(), "hearing", m.group(1) + " Public Hearing"))

    for m in ITEM_NUM_RE.finditer(text):
        boundaries.append((m.start(), "item", None))

    for m in BULLET_RE.finditer(text):
        boundaries.append((m.start(), "item", None))

    boundaries.sort(key=lambda b: b[0])
    return boundaries


def detect_disposition(item_text: str) -> tuple[str | None, str | None]:
    committee_ref = None
    disposition = None
    for label, pattern in DISPOSITION_RULES:
        m = pattern.search(item_text)
        if m:
            disposition = label
            if label == "referred":
                committee_ref = m.group(1).strip()
            break
    return disposition, committee_ref


def extract_remarks(item_text: str, extract_names) -> list[tuple[str, str, str]]:
    """Returns (remark_type, last_name, snippet) tuples."""
    remarks = []
    for remark_type, pattern in REMARK_PATTERNS:
        for m in pattern.finditer(item_text):
            for name in extract_names(m.group(1)):
                snippet = item_text[max(0, m.start() - 40): m.end() + 40].strip()
                remarks.append((remark_type, name, snippet))
    return remarks


def parse_committee_of_whole_meeting(raw_text: str, known_last_names: set[str]) -> dict:
    """Parser for the shorter, separately-formatted 'Committee of the
    Whole' standalone minutes (see format notes above COW_HEADER_RE).
    Unlike parse_meeting(), item boundaries here are just paragraph breaks
    in the source text -- there's no consistent section/committee
    structure to key off of since the whole document already IS one
    committee's minutes, and about half the real examples don't even
    number their paragraphs."""
    extract_names = make_extract_names(known_last_names)
    full_flat = normalize(raw_text)
    result = {"attendance": [], "items": []}

    att_m = COW_ATTENDANCE_RE.search(full_flat)
    if att_m:
        for name in extract_names(att_m.group("present") or ""):
            result["attendance"].append((name, "present"))
        for name in extract_names(att_m.group("absent") or ""):
            result["attendance"].append((name, "absent"))

    raw_paragraphs = [p for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
    for raw_para in raw_paragraphs:
        para = normalize(raw_para)
        para = re.sub(r"^\d{1,2}\.\s?", "", para)  # strip leading "1." where present
        if len(para) < 20 or COW_BOILERPLATE_RE.search(para):
            continue

        votes: dict[str, str] = {}
        vote_type = None
        trigger = COW_ROLLCALL_TRIGGER_RE.search(para)
        if trigger:
            vote_type = "roll_call"
            pos = trigger.end()
            while True:
                cm = COW_ROLLCALL_CLAUSE_RE.match(para, pos)
                if not cm:
                    break
                category, names_raw = cm.group(1).lower(), cm.group(2)
                if "favor" in category:
                    vote = "yes"
                elif "oppose" in category or "against" in category:
                    vote = "no"
                else:  # "absent" or "not present at the ES meeting" etc.
                    vote = "absent"
                for name in extract_names(names_raw):
                    votes[name] = vote
                pos = cm.end()
            pm = COW_PRESIDING_RE.match(para, pos)
            if pm:
                for name in extract_names(pm.group(1)):
                    votes.setdefault(name, "presiding")
        elif "voice vote" in para.lower():
            vote_type = "voice"

        disposition, committee_ref = detect_disposition(para)
        remarks = extract_remarks(para, extract_names)
        sponsor = None
        moved_remarks = [r for r in remarks if r[0] == "moved"]
        if moved_remarks:
            sponsor = moved_remarks[0][1]

        result["items"].append({
            "section": "Committee of the Whole",
            "committee": "Committee of the Whole",
            "description": para[:500],
            "disposition": disposition,
            "sponsor_last_name": sponsor,
            "vote_type": vote_type,
            "votes": votes,
            "remarks": remarks,
        })

    return result


def parse_meeting(text: str, known_last_names: set[str]) -> dict:
    """Parses one meeting's flattened text into attendance + items + votes + remarks."""
    extract_names = make_extract_names(known_last_names)
    flat = normalize(text)

    result = {"attendance": [], "items": []}

    present_label = PRESENT_LABEL_RE.search(flat)
    if present_label:
        present_names, pos = scan_name_list(flat, present_label.end(), known_last_names)
        for name in present_names:
            result["attendance"].append((name, "present"))
        absent_label = ABSENT_LABEL_RE.search(flat, pos, pos + 60)
        if absent_label:
            absent_names, _ = scan_name_list(flat, absent_label.end(), known_last_names)
            for name in absent_names:
                result["attendance"].append((name, "absent"))

    roll_calls = find_roll_calls(flat, extract_names)
    boundaries = find_boundaries(flat)

    section = None
    committee = None
    hearing_label = None

    for i, (pos, kind, label) in enumerate(boundaries):
        if kind == "section":
            section = label
            committee = None
            hearing_label = None
            continue
        if kind == "committee":
            committee = label
            continue
        if kind == "hearing":
            section = "Public Hearing"
            committee = None
            hearing_label = label
            # Public hearings have no numbered/bulleted sub-items of their
            # own -- the whole hearing (through the next boundary) is one
            # logical item, so fall through and create it here too.

        # kind == "item" (or "hearing", see above): text runs from here to
        # the next boundary (or end of text).
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(flat)
        item_text = BULLET_RE.sub("", flat[pos:end]).strip()
        item_text = re.sub(r"^\d{1,2}\.\s?", "", item_text)
        if len(item_text) < 15:
            continue

        current_section = section or "Preamble"
        description = item_text[:500]
        if hearing_label and section == "Public Hearing":
            description = f"{hearing_label}: {description}"

        disposition, committee_ref = detect_disposition(item_text)

        overlapping = [rc for rc in roll_calls if pos <= rc["start"] < end]
        vote_type = "roll_call" if overlapping else ("voice" if "voice vote" in item_text else None)

        remarks = extract_remarks(item_text, extract_names)
        sponsor = None
        moved_remarks = [r for r in remarks if r[0] == "moved"]
        if moved_remarks:
            sponsor = moved_remarks[0][1]

        result["items"].append({
            "section": current_section,
            "committee": committee if current_section == "Committee Reports" else committee_ref,
            "description": description,
            "disposition": disposition,
            "sponsor_last_name": sponsor,
            "vote_type": vote_type,
            "votes": overlapping[0]["votes"] if overlapping else {},
            "remarks": remarks,
        })

    return result


def store_parsed_meeting(conn, meeting_id: int, parsed: dict, name_to_id: dict[str, int]) -> None:
    conn.execute("DELETE FROM votes WHERE agenda_item_id IN (SELECT id FROM agenda_items WHERE meeting_id = ?)", (meeting_id,))
    conn.execute("DELETE FROM remarks WHERE agenda_item_id IN (SELECT id FROM agenda_items WHERE meeting_id = ?)", (meeting_id,))
    conn.execute("DELETE FROM agenda_items WHERE meeting_id = ?", (meeting_id,))
    conn.execute("DELETE FROM attendance WHERE meeting_id = ?", (meeting_id,))

    for last_name, status in parsed["attendance"]:
        cid = name_to_id.get(last_name)
        if cid:
            conn.execute(
                "INSERT INTO attendance (meeting_id, councilor_id, status) VALUES (?, ?, ?)",
                (meeting_id, cid, status),
            )

    for item in parsed["items"]:
        sponsor_id = name_to_id.get(item["sponsor_last_name"]) if item["sponsor_last_name"] else None
        cur = conn.execute(
            """
            INSERT INTO agenda_items (meeting_id, section, description, disposition, committee, sponsor_councilor_id, vote_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (meeting_id, item["section"], item["description"], item["disposition"], item["committee"], sponsor_id, item["vote_type"]),
        )
        item_id = cur.lastrowid

        for last_name, vote in item["votes"].items():
            cid = name_to_id.get(last_name)
            if cid:
                conn.execute(
                    "INSERT INTO votes (agenda_item_id, councilor_id, vote) VALUES (?, ?, ?)",
                    (item_id, cid, vote),
                )

        for remark_type, last_name, snippet in item["remarks"]:
            cid = name_to_id.get(last_name)
            if cid:
                conn.execute(
                    "INSERT INTO remarks (agenda_item_id, councilor_id, remark_type, snippet) VALUES (?, ?, ?, ?)",
                    (item_id, cid, remark_type, snippet),
                )


def main() -> None:
    conn = get_conn()
    known = {row["last_name"] for row in conn.execute("SELECT last_name FROM councilors").fetchall()}
    if not known:
        raise SystemExit("No councilors seeded -- run seed_councilors.py first")
    name_to_id = {row["last_name"]: row["id"] for row in conn.execute("SELECT id, last_name FROM councilors").fetchall()}

    rows = conn.execute("SELECT id, doc_id, raw_text FROM meetings WHERE raw_text IS NOT NULL AND raw_text != ''").fetchall()
    now = datetime.now(timezone.utc).isoformat()
    n_items, n_votes, n_roll_call_items, n_cow = 0, 0, 0, 0
    for row in rows:
        is_cow = bool(COW_HEADER_RE.match(row["raw_text"].strip()))
        if is_cow:
            parsed = parse_committee_of_whole_meeting(row["raw_text"], known)
            n_cow += 1
        else:
            parsed = parse_meeting(row["raw_text"], known)
        store_parsed_meeting(conn, row["id"], parsed, name_to_id)
        conn.execute("UPDATE meetings SET parsed_at = ? WHERE id = ?", (now, row["id"]))
        n_items += len(parsed["items"])
        n_votes += sum(len(item["votes"]) for item in parsed["items"])
        n_roll_call_items += sum(1 for item in parsed["items"] if item["vote_type"] == "roll_call")
    conn.commit()
    conn.close()
    print(
        f"Parsed {len(rows)} meetings ({n_cow} standalone Committee of the Whole minutes): "
        f"{n_items} agenda items, {n_roll_call_items} roll-call items, {n_votes} individual votes recorded"
    )


if __name__ == "__main__":
    main()
