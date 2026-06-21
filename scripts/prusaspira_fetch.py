#!/usr/bin/env python3
"""
Enumerate and cache all entries from prusaspira.org/wirdeins.

Strategy: fetch one page per letter of the Prussian alphabet.
Each page contains ALL entries starting with that letter, including
full declension tables. No per-word lookup needed.

Each letter response is cached as raw/prusaspira/by_letter/{letter}.html.

Entry structure in response:
  <b class='wirds'>lemma</b>              ← visible headword
  <b style='display:none' class='dnum'>lemma <paradigm></b>  ← hidden: word + paradigm

Extended mode: fetch individual declension tables (comparative, superlative,
participle full declensions) via the string= endpoint.
Cached as raw/prusaspira/string/{form}_{paradigm}_{type}.html

Usage:
  python3 scripts/prusaspira_fetch.py                    # Run / resume
  python3 scripts/prusaspira_fetch.py --status           # Show progress
  python3 scripts/prusaspira_fetch.py --test             # Fetch just z and ž
  python3 scripts/prusaspira_fetch.py --extended         # Fetch extended forms
  python3 scripts/prusaspira_fetch.py --extended --test  # Fetch 5 extended forms
  python3 scripts/prusaspira_fetch.py --extended-status  # Show extended progress
"""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import os
import sys

BASE = "https://www.prusaspira.org/wirdeins"
PARAMS = {"akc": "Iz", "tap": "W", "bila": "1"}
DELAY = 2.0

# Prusaspira is diacritic-agnostic on the initial letter:
# wirds=a and wirds=ā return identical results, same for s/š, e/ē etc.
# Use only base letters to avoid duplicate fetches.
ALPHABET = [
    "a", "b", "c", "d", "e", "f", "g", "h",
    "i", "j", "k", "l", "m", "n", "o", "p",
    "r", "s", "t", "u", "v", "w", "z",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "state", "prusaspira_state.json")
OUT_DIR = os.path.join(ROOT, "raw", "prusaspira", "by_letter")
STRING_DIR = os.path.join(ROOT, "raw", "prusaspira", "string")

# Valid extended form types
STRING_TYPES = {"cp", "sp", "pcps", "pcptac", "pcptpa"}


def out_path(letter):
    # Letters with diacritics are valid in Linux filenames
    return os.path.join(OUT_DIR, f"{letter}.html")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"done": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_letter(letter):
    """Fetch all entries starting with `letter` from prusaspira."""
    q = dict(PARAMS, wirds=letter)
    url = f"{BASE}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PrussianCorpusScraper/1.0 (linguistic research)"},
    )
    response = urllib.request.urlopen(req, timeout=60)
    return response.read().decode("utf-8", "replace")


def count_entries(html):
    """Count entries in a prusaspira page via dnum tags."""
    return len(re.findall(r"class='dnum'>([^>]+<\d+>)</b>", html))


# ── Extended string= endpoint helpers ──────────────────────────────


def extract_string_tuples():
    """Scan all cached letter files for ens_str() calls.

    Returns a dict: {key: info} where
      key = "form,paradigm,type"
      info = {"form": str, "paradigm": str, "type": str, "lemmas": set[str]}
    """
    seen = {}
    pattern = re.compile(r"ens_str\('([^']+)'\)")
    for letter in ALPHABET:
        path = out_path(letter)
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        for m in pattern.finditer(html):
            full = m.group(1)  # form,paradigm,type,lemma
            parts = full.split(",")
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
    return seen


def string_filename(form, paradigm, typ):
    """Generate a deterministic filename for a cached string response."""
    safe_form = form.replace(" ", "_")
    return f"{safe_form}_{paradigm}_{typ}.html"


def string_out_path(form, paradigm, typ):
    return os.path.join(STRING_DIR, string_filename(form, paradigm, typ))


def fetch_string(form, paradigm, typ, lemma):
    """Fetch a single extended form table via the string= endpoint."""
    tup = f"{form},{paradigm},{typ},{lemma}"
    q = {"string": tup, "tap": "W", "bila": "1", "wirds": ""}
    url = f"{BASE}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PrussianCorpusScraper/1.0 (linguistic research)"},
    )
    response = urllib.request.urlopen(req, timeout=60)
    return response.read().decode("utf-8", "replace")


def count_strings_from_letters():
    """Count total ens_str calls per type in letter files."""
    counts = {t: 0 for t in STRING_TYPES}
    pattern = re.compile(r"ens_str\('([^']+)'\)")
    for letter in ALPHABET:
        path = out_path(letter)
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        for m in pattern.finditer(html):
            parts = m.group(1).split(",")
            if len(parts) >= 3 and parts[2] in STRING_TYPES:
                counts[parts[2]] += 1
    return counts


def fetch_extended(test=False, paradigms=0):
    """Fetch extended form tables from the string= endpoint.

    Args:
        test: If True, fetch one per type (5 total).
        paradigms: If > 0, fetch up to N examples per (paradigm, type) combo.
    """
    os.makedirs(STRING_DIR, exist_ok=True)

    tuples = extract_string_tuples()
    keys = sorted(tuples.keys())

    if paradigms > 0:
        by_paradigm = {}
        for k in keys:
            p = tuples[k]["paradigm"]
            t = tuples[k]["type"]
            by_paradigm.setdefault(f"{p},{t}", []).append(k)

        picked = []
        for pk in sorted(by_paradigm):
            members = sorted(by_paradigm[pk])
            picked.extend(members[:paradigms])
        keys = picked
    elif test:
        test_keys = []
        for typ in ["cp", "sp", "pcps", "pcptac", "pcptpa"]:
            for k in keys:
                if k.endswith(f",{typ}"):
                    test_keys.append(k)
                    break
        keys = test_keys

    remaining = [k for k in keys
                 if not os.path.exists(string_out_path(tuples[k]["form"], tuples[k]["paradigm"], tuples[k]["type"]))]
    total = len(keys)

    existing = len(keys) - len(remaining)
    print(f"Extended forms: {existing}/{total} cached, {len(remaining)} to fetch now",
          file=sys.stderr)

    for i, key in enumerate(remaining):
        info = tuples[key]
        form, paradigm, typ = info["form"], info["paradigm"], info["type"]
        path = string_out_path(form, paradigm, typ)
        try:
            first_lemma = sorted(info["lemmas"])[0]
            html = fetch_string(form, paradigm, typ, first_lemma)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            size_kb = len(html) // 1024
            print(f"  [{i+1}/{len(remaining)}] {string_filename(form, paradigm, typ)} ({size_kb}KB)",
                  file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  [ERROR] {key}: {e}", file=sys.stderr, flush=True)

        if i + 1 < len(remaining):
            time.sleep(DELAY)

    cached_now = sum(1 for k in keys if os.path.exists(string_out_path(tuples[k]["form"], tuples[k]["paradigm"], tuples[k]["type"])))
    print(f"\nDone. {cached_now}/{total} forms cached in {STRING_DIR}", file=sys.stderr)


def show_extended_status():
    """Show progress on extended form fetching (based on actual files on disk)."""
    tuples = extract_string_tuples()
    total = len(tuples)

    by_type = {}
    cached_by_type = {}
    for k, info in tuples.items():
        typ = info["type"]
        by_type[typ] = by_type.get(typ, 0) + 1
        if os.path.exists(string_out_path(info["form"], info["paradigm"], info["type"])):
            cached_by_type[typ] = cached_by_type.get(typ, 0) + 1

    raw_counts = count_strings_from_letters()
    total_cached = sum(cached_by_type.values())

    print(f"Extended forms: {total_cached}/{total} unique tuples cached")
    print()
    print(f"  {'Type':<8} {'Unique':>8} {'Cached':>8} {'Raw(ltrs)':>10}")
    print(f"  {'----':<8} {'------':>8} {'------':>8} {'---------':>10}")
    for typ in ["cp", "sp", "pcps", "pcptac", "pcptpa"]:
        n = by_type.get(typ, 0)
        c = cached_by_type.get(typ, 0)
        r = raw_counts.get(typ, 0)
        print(f"  {typ:<8} {n:>8} {c:>8} {r:>10}")
    print(f"\n  Total cached: {total_cached} files in {STRING_DIR}")


def run(test=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    state = load_state()
    done = set(state.get("done", []))

    letters = ["z"] if test else ALPHABET
    remaining = [l for l in letters if l not in done and not os.path.exists(out_path(l))]

    total = len(letters)
    print(f"Prusaspira: {len(done)}/{total} letters done, {len(remaining)} remaining",
          file=sys.stderr)

    for i, letter in enumerate(remaining):
        try:
            html = fetch_letter(letter)
            path = out_path(letter)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            n = count_entries(html)
            done.add(letter)
            state["done"] = sorted(done)
            save_state(state)
            print(f"  [{len(done)}/{total}] '{letter}': {n} entries, {len(html)//1024}KB",
                  file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  [ERROR] '{letter}': {e}", file=sys.stderr, flush=True)

        if i + 1 < len(remaining):
            time.sleep(DELAY)

    print(f"\nDone. {len(done)} letters cached in {OUT_DIR}", file=sys.stderr)


def show_status():
    state = load_state()
    done = set(state.get("done", []))
    total_entries = 0

    print(f"Letters done: {len(done)}/{len(ALPHABET)}")
    for letter in ALPHABET:
        path = out_path(letter)
        if os.path.exists(path):
            html = open(path, encoding="utf-8").read()
            n = count_entries(html)
            total_entries += n
            mark = "✓" if letter in done else "~"
            print(f"  {mark} '{letter}': {n} entries, {len(html)//1024}KB")
        else:
            print(f"  - '{letter}': not fetched")
    print(f"Total entries across cached letters: {total_entries}")


if __name__ == "__main__":
    if "--extended-status" in sys.argv:
        show_extended_status()
    elif "--extended" in sys.argv:
        paradigms = 0
        for arg in sys.argv:
            if arg.startswith("--paradigms="):
                paradigms = int(arg.split("=", 1)[1])
            elif arg == "--paradigms":
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    paradigms = int(sys.argv[idx + 1])
        fetch_extended(test="--test" in sys.argv, paradigms=paradigms)
    elif "--status" in sys.argv:
        show_status()
    elif "--test" in sys.argv:
        run(test=True)
    else:
        run()
