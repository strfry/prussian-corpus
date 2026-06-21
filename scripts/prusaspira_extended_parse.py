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
    _adj_columns,
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
    unique (form, paradigm, type) → all associated lemmas.
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

    # Map filename → info
    file_map = {}
    for info in seen.values():
        fname = f"{info['form'].replace(' ', '_')}_{info['paradigm']}_{info['type']}.html"
        info["lemmas"] = sorted(info["lemmas"])
        file_map[fname] = info
    return file_map


def find_declension_table(html):
    soup = BeautifulSoup(html, "html.parser")
    boldtable = soup.find("table", class_="boldtable")
    if not boldtable:
        return None
    for inner in boldtable.find_all("table"):
        if _adj_columns(inner):
            return _parse_adj_declension(inner)
    return None


def parse_type_from_html(html):
    m = re.search(r"<b>[^<]+</b>\s*-\s*(.*?)ezze\s+[^:]+:", html, re.DOTALL)
    if not m:
        return None
    desc = m.group(1).strip()
    if "kōmparatiws" in desc:
        return "cp"
    elif "superlatīws" in desc:
        return "sp"
    elif "particīps" in desc:
        if "pasīws" in desc:
            return "pcptpa"
        elif "aktīws" in desc:
            return "pcptac"
        else:
            return "pcps"
    return None


def merge_into_entry(entry, form, typ, declension, force):
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
                return True
        participles.append({
            "type": part_name,
            "form": form,
            "full_declension": declension,
        })
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

        info = file_map.get(fname)
        if not info:
            stats["orphaned"] += 1
            continue

        path = os.path.join(STRING_DIR, fname)
        html = open(path, encoding="utf-8").read()

        typ = parse_type_from_html(html)
        if not typ:
            stats["errors"] += 1
            print(f"  [SKIP] {fname}: could not determine type", file=sys.stderr)
            continue

        declension = find_declension_table(html)
        if not declension:
            stats["errors"] += 1
            print(f"  [SKIP] {fname}: no valid declension table", file=sys.stderr)
            continue

        stats["parsed"] += 1

        form = info["form"]
        merged_any = False
        for lemma in info["lemmas"]:
            matches = entry_index.get(lemma.lower(), [])
            if not matches:
                if lemma not in stats["not_found"]:
                    stats["not_found"].append(lemma)
                continue
            for idx, entry in matches:
                if merge_into_entry(entry, form, typ, declension, force):
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
    print(f"  → cp/sp merged:   {stats['merged_cp_sp']}", file=sys.stderr)
    print(f"  → pcp merged:     {stats['merged_pcp']}", file=sys.stderr)
    print(f"Errors:             {stats['errors']}", file=sys.stderr)
    if stats["not_found"]:
        print(f"Lemmas not found:   {len(stats['not_found'])}", file=sys.stderr)


if __name__ == "__main__":
    force = "--force" in sys.argv
    stats_only = "--stats" in sys.argv
    run(force=force, stats_only=stats_only)
