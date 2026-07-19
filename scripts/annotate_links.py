#!/usr/bin/env python3
"""
Global inverse pass: add linked and derived_terms fields to every entry.

For a given parsed JSON file (list of entries), adds two fields to every entry
(default []):
  linked        = inverse of ref:         entries whose ref contains this entry's word
  derived_terms = inverse of derived_from: entries whose derived_from contains this entry's word

Runs on both parsed/twanksta_entries.json and parsed/prusaspira_entries.json.

Usage:
  python3 scripts/annotate_links.py
"""

import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    os.path.join(ROOT, "parsed", "twanksta_entries.json"),
    os.path.join(ROOT, "parsed", "prusaspira_entries.json"),
]


def annotate_file(path):
    if not os.path.exists(path):
        print(f"  SKIP {path}: not found", file=sys.stderr)
        return

    with open(path, encoding="utf-8") as f:
        entries = json.load(f)

    # Build lower(word) -> list of entry indices
    word_to_indices = defaultdict(list)
    for i, entry in enumerate(entries):
        wl = entry["word"].lower()
        word_to_indices[wl].append(i)

    # Initialize linked / derived_terms as empty lists
    for entry in entries:
        entry["linked"] = []
        entry["derived_terms"] = []

    # Invert ref: for each source entry, for each target in ref,
    # add source word to target's linked
    for entry in entries:
        for target in entry.get("ref", []):
            tl = target.lower()
            for idx in word_to_indices.get(tl, []):
                entries[idx]["linked"].append(entry["word"])

    # Invert derived_from: same logic for derived_terms
    for entry in entries:
        for target in entry.get("derived_from", []):
            tl = target.lower()
            for idx in word_to_indices.get(tl, []):
                entries[idx]["derived_terms"].append(entry["word"])

    # Dedupe + sort
    for entry in entries:
        entry["linked"] = sorted(set(entry["linked"]), key=str.lower)
        entry["derived_terms"] = sorted(set(entry["derived_terms"]), key=str.lower)

    # Detect genuine ref cycles (2-cycles) within this file — warn only
    ref_edges = defaultdict(set)
    for entry in entries:
        wl = entry["word"].lower()
        for target in entry.get("ref", []):
            tl = target.lower()
            if tl in word_to_indices and tl != wl:
                ref_edges[wl].add(tl)
    cycles = {frozenset((a, b))
              for a in ref_edges for b in ref_edges[a]
              if a != b and a in ref_edges.get(b, ())}
    if cycles:
        pairs = ", ".join("<->".join(sorted(c)) for c in sorted(map(tuple, cycles)))
        print(f"  WARN: {os.path.basename(path)}: {len(cycles)} ref 2-cycle(s): {pairs}",
              file=sys.stderr)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"  Annotated {len(entries)} entries in {os.path.basename(path)}", file=sys.stderr)


def main():
    os.makedirs(os.path.join(ROOT, "parsed"), exist_ok=True)
    for path in FILES:
        annotate_file(path)


if __name__ == "__main__":
    main()
