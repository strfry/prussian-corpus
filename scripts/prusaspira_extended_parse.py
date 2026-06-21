#!/usr/bin/env python3
"""
Parse cached string= endpoint responses and merge extended forms
(comparative, superlative, participle full declensions) into prusaspira_entries.json.

Input:  raw/prusaspira/string/*.html  (from prusaspira_fetch.py --extended)
        parsed/prusaspira_entries.json

Output: updated parsed/prusaspira_entries.json

Usage:
  python3 scripts/prusaspira_extended_parse.py           # merge all cached files
  python3 scripts/prusaspira_extended_parse.py --force   # overwrite existing fields
  python3 scripts/prusaspira_extended_parse.py --stats   # dry-run, no write
"""

import json
import os
import re
import sys

from bs4 import BeautifulSoup

from prusaspira_parse import (
    _cell_text,
    _parse_adj_declension,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRIES_FILE = os.path.join(ROOT, "parsed", "prusaspira_entries.json")
LETTER_DIR = os.path.join(ROOT, "raw", "prusaspira", "by_letter")
STRING_DIR = os.path.join(ROOT, "raw", "prusaspira", "string")

ALPHABET = "abcdefghijklmnoprstuvwz"
STRING_TYPES = {"cp", "sp", "pcps", "pcptac", "pcptpa"}

TYPE_TO_FIELD = {"cp": "comparative", "sp": "superlative"}
PARTICIPLE_TYPE_MAP = {"pcps": "Present", "pcptac": "Past", "pcptpa": "Passive"}

# Filename format: {form}_{paradigm}_{type}.html
FILENAME_RE = re.compile(r'^(.+)_(\d+[a-z]*)_(cp|sp|pcps|pcptac|pcptpa)\.html$')

# Lemma from HTML header: <b>FORM</b> - ... : <b>LEMMA</b>
LEMMA_RE = re.compile(r'<b>[^<]+</b>\s*-[^:]*:\s*<b>([^<]+)</b>')


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_entry_index(entries):
    index = {}
    for i, entry in enumerate(entries):
        word = entry.get("word", "").lower()
        if word:
            index.setdefault(word, []).append((i, entry))
    return index


def build_lemma_map():
    """Scan letter files for all ens_str() calls.

    Returns {filename: {form, paradigm, type, lemmas}} for every
    unique (form, paradigm, type) to all associated lemmas.
    """
    seen = {}
    pattern = re.compile(r"ens_str\('([^']+)'\)")
    for letter in ALPHABET:
        path = os.path.join(LETTER_DIR, f"{letter}.html")
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        for m in pattern.finditer(html):
            parts = m.group(1).split(",")
            if len(parts) < 4:
                continue
            form, paradigm, typ, lemma = parts[0], parts[1], parts[2], parts[3]
            if typ not in STRING_TYPES:
                continue
            key = f"{form},{paradigm},{typ}"
            if key not in seen:
                seen[key] = {"form": form, "paradigm": paradigm, "type": typ, "lemmas": {lemma}}
            else:
                seen[key]["lemmas"].add(lemma)

    # Map filename info
    file_map = {}
    for info in seen.values():
        fname = f"{info['form'].replace(' ', '_')}_{info['paradigm']}_{info['type']}.html"
        info["lemmas"] = sorted(info["lemmas"])
        file_map[fname] = info
    return file_map


def _parse_filename(fname):
    """Parse (form, paradigm, type) from 'abipu{_}aisis_28_cp.html'."""
    m = FILENAME_RE.match(fname)
    if not m:
        return None, None, None
    form = m.group(1).replace('_', ' ')
    return form, m.group(2), m.group(3)


def _extract_lemma_from_html(html):
    """Extract the lemma from '<b>FORM</b> - ... : <b>LEMMA</b>'."""
    m = LEMMA_RE.search(html)
    return m.group(1) if m else None


def _is_adj_table(table_el):
    """Check if table has genderxnumber headers as DIRECT children.

    Uses recursive=False because wrapper tables also contain adjacent
    th elements in their descendants and would otherwise false-match.
    """
    for th in table_el.find_all("th", recursive=False):
        parts = th.get_text(strip=True).split()
        if len(parts) == 2 and parts[0] in ("m", "f", "n") and parts[1] in ("sg", "pl"):
            return True
    return False


def find_declension_tables(html):
    """Find both the indefinite and pronominal (definite) declension tables.

    Returns (indefinite_decl, pronominal_decl_or_None).
    """
    soup = BeautifulSoup(html, "lxml")
    boldtable = soup.find("table", class_="boldtable")
    if not boldtable:
        return None, None

    adj_tables = []
    for inner in boldtable.find_all("table"):
        if _is_adj_table(inner):
            adj_tables.append(_parse_adj_declension(inner))

    if not adj_tables:
        return None, None
    indefinite = adj_tables[0]
    pronominal = adj_tables[1] if len(adj_tables) > 1 else None
    return indefinite, pronominal


def _extract_pcps_adverb(soup):
    """Extract the Adwerban: value from a pcps participle page."""
    for b in soup.find_all("b"):
        if b.get_text(strip=True).rstrip(":") == "Adwerban":
            row = b.find_parent("tr")
            if not row:
                return None
            cells = row.find_all("td", recursive=False)
            if len(cells) >= 2:
                return _cell_text(cells[1])
    return None


def merge_into_entry(entry, form, typ, declension, force,
                     adverb=None, declension_pronominal=None):
    forms = entry.setdefault("forms", {})

    if typ in ("cp", "sp"):
        field = TYPE_TO_FIELD[typ]
        if forms.get(field) and not force:
            return False
        forms[field] = {"word": form, "declension": declension}
        return True

    elif typ in ("pcps", "pcptac", "pcptpa"):
        part_name = PARTICIPLE_TYPE_MAP[typ]
        participles = forms.get("participles", [])
        for p in participles:
            if p.get("type") == part_name:
                if p.get("full_declension") and not force:
                    return False
                p["full_declension"] = declension
                if adverb:
                    p["adverb"] = adverb
                if declension_pronominal:
                    p["full_declension_pronominal"] = declension_pronominal
                return True
        new_part = {
            "type": part_name,
            "form": form,
            "full_declension": declension,
        }
        if adverb:
            new_part["adverb"] = adverb
        if declension_pronominal:
            new_part["full_declension_pronominal"] = declension_pronominal
        participles.append(new_part)
        forms["participles"] = participles
        return True

    return False


def run(force=False, stats_only=False):
    if not os.path.exists(ENTRIES_FILE):
        print(f"ERROR: {ENTRIES_FILE} not found. Run prusaspira_parse.py first.", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(STRING_DIR):
        print(f"ERROR: {STRING_DIR} not found. Run prusaspira_fetch.py --extended first.", file=sys.stderr)
        sys.exit(1)

    entries = load_json(ENTRIES_FILE)
    entry_index = build_entry_index(entries)
    file_map = build_lemma_map()
    string_files = sorted(os.listdir(STRING_DIR))

    stats = {
        "total_files": len(string_files),
        "parsed": 0,
        "merged_cp_sp": 0,
        "merged_pcp": 0,
        "not_found": [],
        "orphaned": 0,
        "errors": 0,
    }

    for fname in string_files:
        if not fname.endswith(".html"):
            continue

        path = os.path.join(STRING_DIR, fname)
        html = open(path, encoding="utf-8").read()

        # Determine type and form: prefer lemma_map, fall back to filename
        info = file_map.get(fname)
        if info:
            typ = info["type"]
            form = info["form"]
        else:
            form, _, typ = _parse_filename(fname)
            if not typ:
                stats["errors"] += 1
                print(f"  [SKIP] {fname}: invalid filename", file=sys.stderr)
                continue

        declension, declension_pronominal = find_declension_tables(html)
        if not declension:
            stats["errors"] += 1
            print(f"  [SKIP] {fname}: no valid declension table", file=sys.stderr)
            continue

        adverb = None
        if typ == "pcps":
            soup = BeautifulSoup(html, "html.parser")
            adverb = _extract_pcps_adverb(soup)

        stats["parsed"] += 1

        # Collect lemmas: from lemma_map, or extract from HTML for orphaned files
        merged_any = False
        if info:
            lemmas = info["lemmas"]
        else:
            lemma = _extract_lemma_from_html(html)
            if lemma:
                lemmas = [lemma]
            else:
                stats["orphaned"] += 1
                continue

        for lemma in lemmas:
            matches = entry_index.get(lemma.lower(), [])
            if not matches:
                if lemma not in stats["not_found"]:
                    stats["not_found"].append(lemma)
                continue
            for idx, entry in matches:
                if merge_into_entry(entry, form, typ, declension, force,
                                    adverb=adverb, declension_pronominal=declension_pronominal):
                    merged_any = True

        if merged_any:
            if typ in ("cp", "sp"):
                stats["merged_cp_sp"] += 1
            else:
                stats["merged_pcp"] += 1

    if stats_only:
        print_stats(stats)
        return

    save_json(ENTRIES_FILE, entries)
    print_stats(stats)
    print(f"\nWrote {ENTRIES_FILE}", file=sys.stderr)
    if stats["not_found"]:
        print(f"Lemmas not in entries: {len(stats['not_found'])}", file=sys.stderr)
        for l in sorted(stats["not_found"])[:15]:
            print(f"  {l}", file=sys.stderr)


def print_stats(stats):
    print(file=sys.stderr)
    print(f"Files in string/:   {stats['total_files']}", file=sys.stderr)
    print(f"  orphaned:         {stats['orphaned']}", file=sys.stderr)
    print(f"Parsed OK:          {stats['parsed']}", file=sys.stderr)
    print(f"  -> cp/sp merged:   {stats['merged_cp_sp']}", file=sys.stderr)
    print(f"  -> pcp merged:     {stats['merged_pcp']}", file=sys.stderr)
    print(f"Errors:             {stats['errors']}", file=sys.stderr)
    if stats["not_found"]:
        print(f"Lemmas not found:   {len(stats['not_found'])}", file=sys.stderr)


if __name__ == "__main__":
    force = "--force" in sys.argv
    stats_only = "--stats" in sys.argv
    run(force=force, stats_only=stats_only)
