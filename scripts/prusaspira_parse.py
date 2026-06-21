#!/usr/bin/env python3
"""
Parse cached prusaspira HTML into a flat JSON list (parsed/prusaspira_entries.json).

Input:  raw/prusaspira/by_letter/{a..z}.html  (23 files)
Output: parsed/prusaspira_entries.json

Schema per entry (mirrors prussian_dictionary.json):
  word, paradigm, gender, desc, audio, translations, forms

Usage:
  python3 scripts/prusaspira_parse.py           # parse all letters
  python3 scripts/prusaspira_parse.py --stats   # print statistics, no output file
  python3 scripts/prusaspira_parse.py --verify  # check cross-refs have no data loss
"""

import json
import os
import re
import sys
import unicodedata

from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(ROOT, "raw", "prusaspira", "by_letter")
OUT_FILE = os.path.join(ROOT, "parsed", "prusaspira_entries.json")

ALPHABET = [
    "a", "b", "c", "d", "e", "f", "g", "h",
    "i", "j", "k", "l", "m", "n", "o", "p",
    "r", "s", "t", "u", "v", "w", "z",
]

# Prusaspira case labels → pd.json case names
PS_CASE_MAP = {
    "Nōm": "Nominative", "Gēn": "Genitive",
    "Dāt": "Dative", "Akk": "Accusative",
}

# Prusaspira column headers → pd.json tense names (indicative) or mood keys
PS_IND_TENSES = {
    "tēntisku": "Present",
    "pragūbingisku": "Habitual",
    "perfektan": "Perfect",
    "perejīngisku": "Future",
}
PS_MOOD_COLS = {"imperatīws": "imperative", "kōnjunktiws": "subjunctive"}

# Prusaspira participle type codes → pd.json type names
PS_PARTICIPLE_TYPES = {
    "pcps":   "Present",
    "pcptac": "Past",
    "pcptpa": "Passive",
}

# Prusaspira person labels → pd.json pronoun strings
PS_PRONOUN_MAP = {
    "As":  "as",
    "Tū":  "tū",
    "3sg": "tāns/tenā/tennan",
    "Mes": "mes",
    "Jūs": "jūs",
    "3pl": "tenēi/tennas",
}


def normalize_initial(ch):
    return unicodedata.normalize("NFD", ch.lower())[0]


def _cell_text(td):
    return " ".join(td.get_text().split()) or None


def _parse_noun_table(table_el):
    """Parse noun case×number table → [{gender: "", cases: [{case, singular, plural}]}]."""
    ths = table_el.find_all("th")
    num_headers = [th.get_text(strip=True) for th in ths if th.get("align") == "left"]

    cases = []
    for tr in table_el.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        case_b = cells[0].find("b")
        if not case_b:
            continue
        case_raw = case_b.get_text(strip=True).rstrip(":")
        case_name = PS_CASE_MAP.get(case_raw, case_raw)

        row = {"case": case_name}
        for i, td in enumerate(cells[1:]):
            val = _cell_text(td) or ""
            if i < len(num_headers):
                h = num_headers[i]
                if h == "sg":
                    row["singular"] = val
                elif h == "pl":
                    row["plural"] = val
        if len(row) > 1:
            cases.append(row)

    return [{"gender": "", "cases": cases}]


def _parse_verb_table_raw(table_raw_html, col_headers):
    """Parse verb table from raw HTML (bypasses BS4 restructuring of malformed tables).

    Prusaspira verb tables have bare <td> elements without <tr> wrappers. BS4 collapses
    them into one implicit row, losing most cells. Raw regex parsing avoids this.
    """
    # indicative: {tense_name: [{pronoun, form}]}
    indicative = {}
    imperative = []
    subjunctive = []
    participles = []

    tds_raw = re.findall(r"<td[^>]*>(.*?)</td>", table_raw_html, re.DOTALL)
    n_cols = len(col_headers)

    i = 0
    while i < len(tds_raw):
        cell_text = re.sub(r"<[^>]+>", "", tds_raw[i]).strip()
        person_raw = cell_text.rstrip(":")
        pronoun = PS_PRONOUN_MAP.get(person_raw)
        if not pronoun:
            i += 1
            continue

        for j, col in enumerate(col_headers):
            if i + 1 + j >= len(tds_raw):
                break
            cell_html = tds_raw[i + 1 + j]

            if col == "particīpai":
                m = re.search(r"ens_str\('([^,]+),\d+[a-z]*,([^,]+)", cell_html)
                if m:
                    ptype = PS_PARTICIPLE_TYPES.get(m.group(2), m.group(2))
                    participles.append({"type": ptype, "form": m.group(1)})
                continue

            val = " ".join(re.sub(r"<[^>]+>", "", cell_html).split())
            if not val:
                continue

            if col in PS_IND_TENSES:
                tense_name = PS_IND_TENSES[col]
                indicative.setdefault(tense_name, []).append({"pronoun": pronoun, "form": val})
            elif col == "imperatīws":
                imperative.append({"pronoun": pronoun, "form": val})
            elif col == "kōnjunktiws":
                subjunctive.append({"pronoun": pronoun, "form": val})

        i += n_cols + 1

    result = {}
    if indicative:
        result["indicative"] = [
            {"tense": tense, "forms": forms}
            for tense, forms in indicative.items()
        ]
    if imperative:
        result["imperative"] = imperative
    if subjunctive:
        result["subjunctive"] = subjunctive
    if participles:
        result["participles"] = participles
    return result


# Adjective declension headers map gender × number, e.g. "m sg" / "f pl".
# Columns are content-driven so both full (m/f/n × sg/pl) and plural-only
# (m pl | f pl | n pl) tables parse correctly.
_NUMBER_NAMES = {"sg": "singular", "pl": "plural"}


def _adj_columns(table_el):
    """Column layout [(gender, number)] read from the gender×number header cells."""
    columns = []
    for th in table_el.find_all("th"):
        parts = th.get_text(strip=True).split()
        if len(parts) == 2 and parts[0] in ("m", "f", "n") and parts[1] in _NUMBER_NAMES:
            columns.append((parts[0], _NUMBER_NAMES[parts[1]]))
    return columns


def _find_adj_table(container):
    """Return the first descendant <table> with a gender×number header layout.

    Adjective entries hold two such tables: the positive declension first, then a
    'Prōnominālas fōrmis' (definite-form) table. We want the first (positive); definite
    forms are not part of the pd.json schema.
    """
    for table in container.find_all("table"):
        if _adj_columns(table):
            return table
    # Plural-only adjectives (m pl | f pl | n pl) have no nested table — the
    # container itself is the declension table.
    if _adj_columns(container):
        return container
    return None


def _parse_adj_declension(table_el):
    """Parse a gender×number adjective table → pd.json gender groups.

    Returns [{gender: "m", cases: [{case, singular, plural}]}, {f...}, {n...}],
    emitting only the number keys that the table actually provides.
    """
    columns = _adj_columns(table_el)
    genders = {}
    case_order = []
    for tr in table_el.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        label = cells[0].find("b")
        if not label:
            continue
        case = PS_CASE_MAP.get(label.get_text(strip=True).rstrip(":"))
        if not case:
            continue
        values = cells[1:1 + len(columns)]
        if len(values) < len(columns):
            continue
        if case not in case_order:
            case_order.append(case)
        for (gender, number), td in zip(columns, values):
            genders.setdefault(gender, {}).setdefault(case, {})[number] = _cell_text(td) or ""

    result = []
    for gender in ("m", "f", "n"):
        if gender not in genders:
            continue
        cases = [dict(case=c, **genders[gender][c]) for c in case_order]
        result.append({"gender": gender, "cases": cases})
    return result


def _extract_adverb(container):
    """Extract the positive adverb from the 'Adwerban:' summary row (first value cell)."""
    for b in container.find_all("b"):
        if b.get_text(strip=True).rstrip(":") == "Adwerban":
            row = b.find_parent("tr")
            if not row:
                return None
            cells = row.find_all("td", recursive=False)
            return _cell_text(cells[1]) if len(cells) >= 2 else None
    return None


def _parse_adj_table(table_el):
    """Parse an adjective entry: positive declension (m/f/n) + adverb.

    Comparative/superlative full declensions are only available via a separate
    string= fetch (see docs/prusaspira_string_endpoint.md) and are attached later.
    """
    forms = {}
    positive = _find_adj_table(table_el)
    if positive:
        decl = _parse_adj_declension(positive)
        if decl:
            forms["declension"] = decl
    adverb = _extract_adverb(table_el)
    if adverb:
        forms["adverb"] = adverb
    return forms


def parse_table(table_el, raw_table_html=""):
    """Dispatch to verb, adjective, or noun table parser."""
    if not table_el:
        return {}

    ths = table_el.find_all("th")
    col_headers = []
    for th in ths:
        txt = th.get_text(strip=True).rstrip(":")
        if txt in PS_IND_TENSES or txt in PS_MOOD_COLS or txt == "particīpai":
            col_headers.append(txt)

    if col_headers:
        return _parse_verb_table_raw(raw_table_html or str(table_el), col_headers)

    if _find_adj_table(table_el):
        return _parse_adj_table(table_el)

    return {"declension": _parse_noun_table(table_el)}


def parse_letter(html, letter):
    # Truncate before the reverse English→Prussian index section
    cutoff = html.find("Na prūsiskan:")
    if cutoff > 0:
        html = html[:cutoff]

    div_match = re.search(r'<div[^>]*id="rezultatai"', html)
    if not div_match:
        return []
    div_start = div_match.start()

    first_entry_m = re.search(r"prūsiskai:\s*", html[div_start:])
    if not first_entry_m:
        return []
    content_start = div_start + first_entry_m.end()

    remaining = html[content_start:]
    chunks = re.split(r"<br\s*/?>\s*prūsiskai:\s*", remaining)

    entries = []
    for chunk in chunks:
        s = BeautifulSoup(chunk, "html.parser")

        wirds_el = s.find("b", class_="wirds")
        if not wirds_el:
            continue
        lemma = wirds_el.get_text(strip=True)
        if not lemma or normalize_initial(lemma[0]) != letter:
            continue  # cross-reference — appears in its own letter file

        dnum_el = s.find("b", class_="dnum")
        dnum_text = dnum_el.get_text(strip=True) if dnum_el else ""
        paradigm_m = re.search(r"<(\d+[a-z]*)>", dnum_text)

        trans_m = re.search(r"ēngliskai:\s*<b>([^<]+)</b>", chunk)

        source_el = s.find("font")
        source = source_el.get_text(strip=True).strip("[]") if source_el else ""

        table_el = s.find("table", class_="boldtable")
        raw_table_m = re.search(
            r"<table[^>]*class=['\"]boldtable['\"][^>]*>.*?</table>",
            chunk, re.DOTALL | re.IGNORECASE
        )
        raw_table_html = raw_table_m.group(0) if raw_table_m else ""
        forms = parse_table(table_el, raw_table_html)

        entries.append({
            "word":         lemma,
            "paradigm":     paradigm_m.group(1) if paradigm_m else "",
            "gender":       "",
            "desc":         source,
            "audio":        "",
            "translations": {"engl": [trans_m.group(1)]} if trans_m else {},
            "forms":        forms,
        })
    return entries


def run():
    os.makedirs(os.path.join(ROOT, "parsed"), exist_ok=True)
    all_entries = []
    for letter in ALPHABET:
        path = os.path.join(IN_DIR, f"{letter}.html")
        if not os.path.exists(path):
            print(f"  SKIP '{letter}': not cached", file=sys.stderr)
            continue
        html = open(path, encoding="utf-8").read()
        entries = parse_letter(html, letter)
        all_entries.extend(entries)
        print(f"  '{letter}': {len(entries)} entries", file=sys.stderr)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(all_entries)} entries to {OUT_FILE}", file=sys.stderr)


def show_stats():
    total = no_forms = no_paradigm = 0
    for letter in ALPHABET:
        path = os.path.join(IN_DIR, f"{letter}.html")
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        entries = parse_letter(html, letter)
        total += len(entries)
        no_forms += sum(1 for e in entries if not e["forms"])
        no_paradigm += sum(1 for e in entries if not e["paradigm"])
        print(f"  '{letter}': {len(entries)} entries")
    print(f"\nTotal: {total}, without forms: {no_forms}, without paradigm: {no_paradigm}")


def verify_cross_refs():
    """Check that every cross-reference appears in its own letter file."""
    missing = []
    for fname in sorted(os.listdir(IN_DIR)):
        letter = fname.replace(".html", "")
        if letter not in ALPHABET:
            continue
        html = open(os.path.join(IN_DIR, fname), encoding="utf-8").read()
        wirds = re.findall(r"class='wirds'>([^<]+)<", html)
        for w in wirds:
            if not w:
                continue
            own_letter = normalize_initial(w[0])
            if own_letter == letter:
                continue
            own_path = os.path.join(IN_DIR, f"{own_letter}.html")
            if not os.path.exists(own_path):
                missing.append((w, own_letter, "file missing"))
                continue
            if w not in open(own_path, encoding="utf-8").read():
                missing.append((w, own_letter, "not found in file"))

    if missing:
        print(f"WARN: {len(missing)} cross-references not found in own letter file:")
        for w, l, reason in missing:
            print(f"  {w!r} → '{l}': {reason}")
    else:
        print("OK: all cross-references appear in their own letter files")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        show_stats()
    elif "--verify" in sys.argv:
        verify_cross_refs()
    else:
        run()
