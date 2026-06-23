#!/usr/bin/env python3
"""
Build the canonical prussian_dictionary.json from parsed source entries.

This is the artefact consumed downstream by prussian-mcp (as the input to
generate_embeddings.py) and prussian-lora (vocab-corpus generation). It is the
single point at which the per-source parses become "the dictionary".

Inputs:
  parsed/twanksta_entries.json     — primary source (Twanksta / wirdeins, semba)
  parsed/prusaspira_entries.json   — supplementary source (Prusaspira), optional

Output:
  parsed/prussian_dictionary.json

By default the dictionary is exactly the Twanksta parse: this is what the
historical mcp scraper produced, so the entry set (and therefore mcp's
embedding alignment) stays stable. Pass --with-prusaspira to union in
Prusaspira lemmas that are absent from Twanksta.

Schema per entry (unchanged from the per-source parses):
  word, paradigm, gender, desc, audio, translations, forms

Usage:
  python3 scripts/build_dictionary.py                   # twanksta-only (canonical)
  python3 scripts/build_dictionary.py --with-prusaspira # union prusaspira-only words
  python3 scripts/build_dictionary.py --stats           # counts, no output file
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSED = os.path.join(ROOT, "parsed")
TWANKSTA = os.path.join(PARSED, "twanksta_entries.json")
PRUSASPIRA = os.path.join(PARSED, "prusaspira_entries.json")
OUT_FILE = os.path.join(PARSED, "prussian_dictionary.json")


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(with_prusaspira=False):
    twanksta = _load(TWANKSTA)
    if twanksta is None:
        sys.exit(f"missing {TWANKSTA} — run `make twanksta-parse` first")

    entries = list(twanksta)
    added = 0
    if with_prusaspira:
        prusaspira = _load(PRUSASPIRA)
        if prusaspira is None:
            sys.exit(f"missing {PRUSASPIRA} — run `make prusaspira-parse` first")
        known = {e.get("word") for e in entries}
        for e in prusaspira:
            if e.get("word") not in known:
                entries.append(e)
                known.add(e.get("word"))
                added += 1

    return entries, added


def main():
    with_prusaspira = "--with-prusaspira" in sys.argv
    entries, added = build(with_prusaspira=with_prusaspira)

    if "--stats" in sys.argv:
        with_forms = sum(1 for e in entries if e.get("forms"))
        with_trans = sum(1 for e in entries if e.get("translations"))
        print(f"Entries:          {len(entries)}")
        print(f"  with forms:     {with_forms}")
        print(f"  with translat.: {with_trans}")
        if with_prusaspira:
            print(f"  prusaspira-only:{added}")
        return

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    msg = f"Wrote {len(entries)} entries to {OUT_FILE}"
    if with_prusaspira:
        msg += f" ({added} from prusaspira)"
    print(msg, file=sys.stderr)


if __name__ == "__main__":
    main()
