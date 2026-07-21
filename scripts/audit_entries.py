#!/usr/bin/env python3
"""
Audit parsed/twanksta_entries.json for data quality issues.

Reads the parsed file, prints a report to stderr. Always exits 0 (report, not gate).

Sections:
  (a) counts: no translation / no forms / no both
  (b) ref resolution rate (per-occurrence, case-insensitive against own wordlist)
  (c) missing ref targets: macron-normalizable vs real corpus gap
  (d) broken paradigms (identity-collapse declension)
  (e) digit-bearing form strings

Usage:
  python3 scripts/audit_entries.py
  python3 scripts/audit_entries.py --word X   # audit single entry, print detail
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_FILE = os.path.join(ROOT, "parsed", "twanksta_entries.json")


def macron_fold(s):
    return s.replace('ā', 'a').replace('ē', 'e').replace('ī', 'i').replace('ō', 'o').replace('ū', 'u')


def _has_digit(obj):
    if isinstance(obj, str):
        return bool(re.search(r'\d', obj))
    if isinstance(obj, list):
        return any(_has_digit(x) for x in obj)
    if isinstance(obj, dict):
        return any(_has_digit(v) for v in obj.values())
    return False


def _is_broken_verb_paradigm(forms, word, paradigm):
    if paradigm not in ("106d", "106b"):
        return False
    if not isinstance(forms, dict):
        return False
    decl = forms.get("declension")
    if not decl or not isinstance(decl, list):
        return False
    for g in decl:
        for c in g.get("cases", []):
            for key in ("singular", "plural"):
                val = c.get(key)
                if val and val != word:
                    return False
    return True


def audit(all_entries, target_word=None):
    entries = all_entries
    if target_word:
        entries = [e for e in all_entries if e["word"] == target_word]
        if not entries:
            print(f"Word '{target_word}' not found", file=sys.stderr)
            return

    words_exact = {e["word"] for e in all_entries}
    words_lower = {w.lower() for w in words_exact}

    def find_lemma(target):
        if target in words_exact:
            return target, "exact"
        if target.lower() in words_lower:
            for w in words_exact:
                if w.lower() == target.lower():
                    return w, "case"
        folded = macron_fold(target).casefold()
        hits = [w for w in words_exact if macron_fold(w).casefold() == folded]
        if len(hits) == 1:
            return hits[0], "macron"
        return None, "unresolved"

    # (a) counts
    no_trans = sum(1 for e in entries if not any(e.get("translations", {}).values()))
    no_forms = sum(1 for e in entries if not e.get("forms"))
    no_both = sum(1 for e in entries if not any(e.get("translations", {}).values()) and not e.get("forms"))

    print(f"(a) no translation: {no_trans}", file=sys.stderr)
    print(f"    no forms:        {no_forms}", file=sys.stderr)
    print(f"    no both:         {no_both}", file=sys.stderr)

    # (b) ref resolution
    total_refs = sum(len(e.get("ref", [])) for e in entries)
    res_exact = 0
    res_case = 0
    res_macron = 0
    unres = 0
    for e in entries:
        for r in e.get("ref", []):
            _, kind = find_lemma(r)
            if kind == "exact":
                res_exact += 1
            elif kind == "case":
                res_case += 1
            elif kind == "macron":
                res_macron += 1
            else:
                unres += 1
    total_resolved = res_exact + res_case + res_macron
    print(f"\n(b) refs: {total_resolved}/{total_refs} resolvable ({res_exact} exact, {res_case} case, {res_macron} macron); {unres} unresolvable", file=sys.stderr)

    # (c) missing ref targets
    macron_normalizable = 0
    real_gap = 0
    for e in entries:
        for r in e.get("ref", []):
            _, kind = find_lemma(r)
            if kind == "macron":
                macron_normalizable += 1
            elif kind == "unresolved":
                real_gap += 1
    print(f"\n(c) missing ref targets: {macron_normalizable} macron-normalizable, {real_gap} real corpus gap", file=sys.stderr)

    # (d) broken paradigms
    broken = []
    for e in entries:
        if _is_broken_verb_paradigm(e.get("forms", {}), e["word"], e.get("paradigm", "")):
            broken.append(f"{e['word']} (p{e['paradigm']})")
    print(f"\n(d) broken paradigms (identity-collapse): {len(broken)}", file=sys.stderr)
    for b in broken:
        print(f"    {b}", file=sys.stderr)

    # (e) digit-bearing form strings
    digit_entries = []
    for e in entries:
        if _has_digit(e.get("forms", {})):
            digit_entries.append(f"{e['word']} (p{e['paradigm']})")
    print(f"\n(e) digit-bearing form strings: {len(digit_entries)}", file=sys.stderr)
    for d in digit_entries:
        print(f"    {d}", file=sys.stderr)

    # Anchor comments (release v2026-07-20)
    print(f"\n--- anchor values (release v2026-07-20) ---", file=sys.stderr)
    print(f"no translation: 970", file=sys.stderr)
    print(f"no both: 964", file=sys.stderr)
    print(f"broken paradigms: 2 (senlaītun, perleītun)", file=sys.stderr)
    print(f"digit artifacts: 3 (izpilninamins, izraūjakiskan, militāriska skalīsnā)", file=sys.stderr)

    # State files (written by parser)
    bp_path = os.path.join(ROOT, "state", "broken_paradigms.json")
    mrt_path = os.path.join(ROOT, "state", "missing_ref_targets.json")
    if os.path.exists(bp_path):
        bp = json.load(open(bp_path, encoding="utf-8"))
        print(f"\nstate/broken_paradigms.json: {len(bp)} entries", file=sys.stderr)
    if os.path.exists(mrt_path):
        mrt = json.load(open(mrt_path, encoding="utf-8"))
        macron_hits = 0
        for item in mrt:
            folded = macron_fold(item["ref"]).casefold()
            hits = [w for w in words_exact if macron_fold(w).casefold() == folded]
            if len(hits) == 1:
                macron_hits += 1
        print(f"state/missing_ref_targets.json: {len(mrt)} entries ({macron_hits} macron-normalizable, {len(mrt) - macron_hits} real gaps)", file=sys.stderr)

    return {
        "no_translation": no_trans,
        "no_forms": no_forms,
        "no_both": no_both,
        "total_refs": total_refs,
        "resolved": total_resolved,
        "unresolved": unres,
        "macron_normalizable": macron_normalizable,
        "real_gap": real_gap,
        "broken": len(broken),
        "digit": len(digit_entries),
    }


if __name__ == "__main__":
    entries = json.load(open(IN_FILE, encoding="utf-8"))
    target = None
    if "--word" in sys.argv:
        idx = sys.argv.index("--word")
        target = sys.argv[idx + 1]
    audit(entries, target)
