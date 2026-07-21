#!/usr/bin/env python3
"""
Parse cached twanksta HTML into a flat JSON list (parsed/twanksta_entries.json).

Input:
  state/twanksta_wordlist.json             — word list with paradigm numbers
  raw/twanksta/entries/{word}/{lang}.html  — search result per word & language
  raw/twanksta/forms/{num}_{word}.html     — paradigm table per lemma

Output:
  parsed/twanksta_entries.json
  state/broken_paradigms.json   — lemmas with identity-collapse declension tables
  state/missing_ref_targets.json — refs unresolved after normalization

Schema per entry (mirrors prussian_dictionary.json):
  word, paradigm, gender, desc, ref, derived_from, audio, translations, forms

Usage:
  python3 scripts/twanksta_parse.py            # parse all
  python3 scripts/twanksta_parse.py --word X   # parse single word, print to stdout
  python3 scripts/twanksta_parse.py --stats    # print counts, no output file
"""

import json
import os
import re
import sys

from bs4 import BeautifulSoup

LANGS = ["engl", "miks", "leit", "latt", "pols", "mask"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDLIST = os.path.join(ROOT, "state", "twanksta_wordlist.json")
ENTRIES_DIR = os.path.join(ROOT, "raw", "twanksta", "entries")
FORMS_DIR = os.path.join(ROOT, "raw", "twanksta", "forms")
OUT_FILE = os.path.join(ROOT, "parsed", "twanksta_entries.json")
BASE_URL = "https://wirdeins.twanksta.org"

_VERB_TENSES = {"Present", "Past", "Perfect", "Future", "Habitual"}

_LEMMA_SET = None
_LEMMA_SET_LOWER = None

_ABBREV = frozenset({
    'MK', 'drv', 'DRV', 'E', 'Nx', 'Pit', 'VM', 'DIA', 'drv,', 'DRV,',
    'GlN', 'I', 'ON', 'DIA,', 'Gr', 'DK', 'II', 'III', 'IV', 'V', 'VI',
    'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'N', 'PN', 'AV', 'CONJ',
    'PREP', 'ADV', 'PRON', 'INTERJ', 'NUM', 'Kr', 'alt', 'alt,', 'Subs',
    'JG', 'JB', 'MBS', 'WE', 'OPN', 'GN', 'TN', 'APN', 'VT',
    'si', 'sin',
})


def _get_lemma_set():
    global _LEMMA_SET, _LEMMA_SET_LOWER
    if _LEMMA_SET is None:
        _LEMMA_SET = set(os.listdir(ENTRIES_DIR))
        _LEMMA_SET_LOWER = {l.lower() for l in _LEMMA_SET}
    return _LEMMA_SET, _LEMMA_SET_LOWER


def macron_fold(s):
    return s.replace('ā', 'a').replace('ē', 'e').replace('ī', 'i').replace('ō', 'o').replace('ū', 'u')


def _normalize_slashes(text):
    return re.sub(r'\s*/\s*', ' / ', text).strip()

def _verb_form(text):
    val = _normalize_slashes(" ".join(text.split()))
    return val or None



def parse_noun_cases(table):
    """Parse a declension table's rows into [{case, singular, plural}] list."""
    headers = [th.get_text(strip=True) for th in table.find_all("th", class_="hea2")]
    cases = []
    for tr in table.find_all("tr"):
        case_th = tr.find("th", class_="hea")
        if not case_th:
            continue
        case_name = case_th.get_text(strip=True)
        cells = tr.find_all("td")
        row = {"case": case_name}
        for i, td in enumerate(cells):
            if i >= len(headers):
                break
            form_el = td.find(class_="verb")
            val = _normalize_slashes(form_el.get_text(strip=True) if form_el else td.get_text(strip=True))
            if headers[i] == "sing":
                row["singular"] = val
            elif headers[i] == "plur":
                row["plural"] = val
        if len(row) > 1:
            cases.append(row)
    return cases


def parse_noun_table(table):
    """Parse a <table id='subst'> into [{gender, cases}] (pd.json declension format)."""
    gender_th = table.find("th", class_="null")
    gender = gender_th.get_text(strip=True) if gender_th else ""
    return [{"gender": gender, "cases": parse_noun_cases(table)}]


def parse_verb_section(soup):
    """Parse all mood sections from verb form HTML into pd.json verb forms dict."""
    result = {}
    html_str = str(soup)

    # Split on H3 mood headings to get named sections
    parts = re.split(r"<h3[^>]*>([^<]+)</h3>", html_str)
    # parts = [pre, mood1, content1, mood2, content2, ...]

    for i in range(1, len(parts), 2):
        mood = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        s = BeautifulSoup(content, "html.parser")

        if mood == "Indicative mood":
            tense_groups = []
            # Scope to table.response to avoid capturing outer layout tds
            resp = s.find("table", class_="response")
            tds = resp.find_all("td") if resp else []
            for td in tds:
                head = td.find(class_="head")
                if not head or head.get_text(strip=True) not in _VERB_TENSES:
                    continue
                tense_name = head.get_text(strip=True)
                pronouns = td.find_all(class_="pronoun")
                verbs = td.find_all(class_="verb")
                forms = [
                    {"pronoun": p.get_text(strip=True), "form": _verb_form(v.get_text())}
                    for p, v in zip(pronouns, verbs)
                    if p.get_text(strip=True) and _verb_form(v.get_text())
                ]
                if forms:
                    tense_groups.append({"tense": tense_name, "forms": forms})
            if tense_groups:
                result["indicative"] = tense_groups

        elif mood == "Optative":
            verbs = s.find_all(class_="verb")
            if verbs:
                val = _verb_form(verbs[0].get_text())
                if val:
                    result["optative"] = val

        elif mood in ("Imperative", "Subjunctive"):
            key = "imperative" if mood == "Imperative" else "subjunctive"
            pronouns = s.find_all(class_="pronoun")
            verbs = s.find_all(class_="verb")
            forms = [
                {"pronoun": p.get_text(strip=True), "form": _verb_form(v.get_text())}
                for p, v in zip(pronouns, verbs)
                if p.get_text(strip=True) and _verb_form(v.get_text())
            ]
            if forms:
                result[key] = forms

    # Participles from spoiler-body2 divs
    participles = []
    for el in soup.find_all("div", class_="spoiler-body2"):
        title_el = el.find_previous(class_="spoiler-title2")
        if not title_el:
            continue
        stem = title_el.get_text(strip=True).lstrip("►").strip()
        tables = el.find_all("table", id="subst")
        if not tables:
            continue
        full_declension = []
        nom_sg = ""
        for table in tables:
            parsed = parse_noun_table(table)
            if parsed:
                full_declension.extend(parsed)
                if not nom_sg:
                    for gender_entry in parsed:
                        for case_entry in gender_entry.get("cases", []):
                            if case_entry.get("case") == "Nominative":
                                nom_sg = case_entry.get("singular", "")
                                break
                        if nom_sg:
                            break
        if nom_sg:
            head = title_el.find_previous("span", class_="head")
            pcpt_type = head.get_text(strip=True) if head else "Passive"
            entry = {"type": pcpt_type, "form": nom_sg}
            if full_declension:
                entry["full_declension"] = full_declension
            participles.append(entry)
    if participles:
        result["participles"] = participles

    return result


def parse_adj_tables(soup):
    """Parse adjective positive + comparison tables into pd.json nested format."""
    DEGREE_KEYS = ["declension", "comparative", "superlative"]
    degree_idx = 0
    group_count = 0
    degree_groups: list = [[], [], []]

    for table in soup.find_all("table", id="subst"):
        gender_th = table.find("th", class_="null")
        gender_text = gender_th.get_text(strip=True) if gender_th else ""
        if not gender_text:
            continue  # Skip summary/adverb tables
        gender = gender_text[0].lower()  # m/f/n
        if degree_idx >= 3:
            break
        degree_groups[degree_idx].append({"gender": gender, "cases": parse_noun_cases(table)})
        group_count += 1
        if group_count == 3:
            degree_idx += 1
            group_count = 0

    forms = {}
    for i, key in enumerate(DEGREE_KEYS):
        if degree_groups[i]:
            forms[key] = degree_groups[i]

    # Adverb table: extract positive, comparative, superlative adverb forms
    for table in soup.find_all("table", id="subst"):
        hea2 = [th.get_text(strip=True) for th in table.find_all("th", class_="hea2")]
        if "Adverb" not in hea2:
            continue
        adverb = {}
        col_map = {"positive": "Adverb", "comparative": "Comparative", "superlative": "Superlative"}
        col_indices = {}
        for key, label in col_map.items():
            if label in hea2:
                col_indices[key] = hea2.index(label)
        if not col_indices:
            break
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            any_found = False
            for key, idx in col_indices.items():
                if idx < len(cells):
                    el = cells[idx].find(class_="verb")
                    if el:
                        adverb[key] = el.get_text(strip=True)
                        any_found = True
            if any_found:
                forms["adverb"] = adverb
                break
        break

    return forms


def parse_forms(html):
    """Dispatch to appropriate parser based on form HTML content."""
    if not html or not html.strip():
        return {}
    # Fix malformed HTML: stray </td> before neut table pushes it outside spoiler-body2
    html = re.sub(r'</td>\s*</td>', '</td>', html)
    soup = BeautifulSoup(html, "html.parser")
    is_verb = bool(soup.find("h3", string=re.compile("Indicative|Optative|Imperative|Subjunctive")))
    if is_verb:
        return parse_verb_section(soup)
    subst_tables = soup.find_all("table", id="subst")
    if not subst_tables:
        return {}
    has_comparison = bool(soup.find(class_="spoiler-body2"))
    if has_comparison or len(subst_tables) > 1:
        return parse_adj_tables(soup)
    return {"declension": parse_noun_table(subst_tables[0])}


def _strip_paradigm_suffix(val, paradigm):
    if not paradigm or not isinstance(val, str):
        return val
    parts = val.split(' / ')
    cleaned = []
    for part in parts:
        p = part.strip()
        m = re.search(re.escape(paradigm), p)
        if m:
            suffix = p[m.start():]
            remaining = p[:m.start()]
            if remaining.strip():
                p = remaining.strip()
        cleaned.append(p)
    return ' / '.join(cleaned)


def _clean_forms(forms, paradigm):
    if isinstance(forms, str):
        return _strip_paradigm_suffix(forms, paradigm)
    if isinstance(forms, list):
        return [_clean_forms(item, paradigm) for item in forms]
    if isinstance(forms, dict):
        return {k: _clean_forms(v, paradigm) for k, v in forms.items()}
    return forms


def extract_desc_refs(desc_el, word):
    if not desc_el:
        return "", [], []
    lemma_set, lemma_set_lower = _get_lemma_set()
    refs = []
    derived_from = []
    seen = set()
    seen_derived = set()

    def add_ref(r):
        if r not in seen:
            seen.add(r)
            refs.append(r)

    def add_derived(r):
        if r not in seen_derived:
            seen_derived.add(r)
            derived_from.append(r)

    # Layer 1: refs from <a> links
    for a in desc_el.find_all("a"):
        ref_text = a.get_text(strip=True)
        if ref_text.lower() != word.lower():
            add_ref(ref_text)
    for a in desc_el.find_all("a"):
        a.extract()

    text = desc_el.get_text(" ", strip=True)

    # Layer 2: refs from non-linked ↑<text> (no space between ↑ and text)
    def capture_up_ref(m):
        lemma = m.group(1).strip()
        if lemma.lower() != word.lower():
            add_ref(lemma)
        return ''
    text = re.sub(r'↑(\S[^[]*?)(?=\s*\[|\s*$)', capture_up_ref, text)

    # Remove orphaned ↑ left from extracted <a> links
    text = re.sub(r'↑', ' ', text)

    # Clean whitespace after removals
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([,;])', r'\1', text)
    text = text.strip(" ,;")

    # Layer 3: derived_from from [] source citations (tokens matching known lemmas)
    for m in re.finditer(r'\[([^\]]+)\]', text):
        content = m.group(1)
        for t in re.split(r'[\s+]+', content):
            t = t.strip(' ,.')
            if not t:
                continue
            if t in _ABBREV:
                continue
            if any(c.isdigit() for c in t):
                continue
            if len(t) <= 1:
                continue
            if t.lower() == word.lower():
                continue
            if t in lemma_set or t.lower() in lemma_set_lower:
                add_derived(t)

    return text, refs, derived_from


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


def parse_entry_li(li):
    """Extract fields from a single <li> element (one search result)."""
    word_el = li.find(class_="word")
    if not word_el:
        return None

    numb_el = li.find(class_="numb")
    gend_el = li.find(class_="gend")
    desc_el = li.find(class_="desc")
    audio_el = li.find("audio")

    translations = []
    for child in li.find_all(class_="translation-child"):
        num_el = child.find(class_="translation-number")
        if num_el:
            num_el.decompose()
        t = child.get_text(strip=True)
        if t:
            translations.append(t)

    word_text = word_el.get_text(strip=True)
    desc_text, refs, derived_from = extract_desc_refs(desc_el, word_text) if desc_el else ("", [], [])

    return {
        "word": word_text,
        "paradigm": numb_el.get_text(strip=True) if numb_el else "",
        "gender": gend_el.get_text(strip=True) if gend_el else "",
        "desc": desc_text,
        "ref": refs,
        "derived_from": derived_from,
        "audio": BASE_URL + audio_el["src"] if audio_el and audio_el.get("src") else "",
        "translations": translations,
    }


def parse_all_li(html):
    """Parse all <li> search results from HTML, return list of entry dicts."""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for li in soup.find_all("li"):
        entry = parse_entry_li(li)
        if entry:
            entries.append(entry)
    return entries


def parse_word(word, paradigm):
    """Parse all cached files for one search word, return list of entry dicts.

    A single search result may contain multiple <li> entries (different
    words matched by the search engine). All of them are returned.
    Duplicates across different search words are handled by the caller.
    """
    entry_dir = os.path.join(ENTRIES_DIR, word)
    if not os.path.isdir(entry_dir):
        return []
    engl_path = os.path.join(entry_dir, "engl.html")
    if not os.path.exists(engl_path):
        return []

    with open(engl_path, encoding="utf-8") as f:
        bases = parse_all_li(f.read())
    if not bases:
        return []

    translations_per_entry = [{"engl": b.pop("translations")} for b in bases]

    for lang in LANGS[1:]:
        p = os.path.join(entry_dir, f"{lang}.html")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            other_entries = parse_all_li(f.read())
        for i, base in enumerate(bases):
            for other in other_entries:
                if other["word"] == base["word"]:
                    translations_per_entry[i][lang] = other["translations"]
                    break

    entries = []
    for base, translations in zip(bases, translations_per_entry):
        forms = {}
        if base["paradigm"]:
            form_path = os.path.join(FORMS_DIR, f"{base['paradigm']}_{base['word']}.html")
            if os.path.exists(form_path):
                with open(form_path, encoding="utf-8") as f:
                    forms = parse_forms(f.read())
                forms = _clean_forms(forms, base["paradigm"])

        entries.append({
            "word": base["word"],
            "paradigm": base["paradigm"],
            "gender": base["gender"],
            "desc": base["desc"],
            "ref": base["ref"],
            "derived_from": base["derived_from"],
            "audio": base["audio"],
            "translations": translations,
            "forms": forms,
        })

    return entries


def run(target_word=None):
    os.makedirs(os.path.join(ROOT, "parsed"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
    wordlist = json.load(open(WORDLIST, encoding="utf-8"))
    if target_word:
        wordlist = [w for w in wordlist if w["word"] == target_word]

    entries = []
    seen = set()
    skipped = 0
    for item in wordlist:
        word = item["word"]
        paradigm = item.get("paradigm", "")
        new_entries = parse_word(word, paradigm)
        if not new_entries:
            skipped += 1
            continue
        for entry in new_entries:
            key = (entry["word"], entry["paradigm"], entry["desc"])
            if key not in seen:
                seen.add(key)
                entries.append(entry)

    if target_word:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    # Broken-paradigm detection: verb paradigms with identity-collapse declension
    broken_paradigms = []
    for entry in entries:
        if _is_broken_verb_paradigm(entry["forms"], entry["word"], entry["paradigm"]):
            broken_paradigms.append({
                "word": entry["word"],
                "paradigm": entry["paradigm"],
            })
            entry["forms"] = {}
    if broken_paradigms:
        with open(os.path.join(ROOT, "state", "broken_paradigms.json"), "w", encoding="utf-8") as f:
            json.dump(broken_paradigms, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(broken_paradigms)} broken paradigms to state/broken_paradigms.json", file=sys.stderr)

    # Ref normalization pass: macron/casefold resolution
    words_exact = {e["word"] for e in entries}
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

    missing_ref_targets = []
    for entry in entries:
        normalized = []
        for ref in entry["ref"]:
            resolved, kind = find_lemma(ref)
            if resolved:
                normalized.append(resolved)
            else:
                missing_ref_targets.append({
                    "ref": ref,
                    "source_word": entry["word"],
                })
        entry["ref"] = normalized

    if missing_ref_targets:
        with open(os.path.join(ROOT, "state", "missing_ref_targets.json"), "w", encoding="utf-8") as f:
            json.dump(missing_ref_targets, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(missing_ref_targets)} missing ref targets to state/missing_ref_targets.json", file=sys.stderr)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(entries)} entries to {OUT_FILE}", file=sys.stderr)
    if skipped:
        print(f"Skipped {skipped} words (no cached files)", file=sys.stderr)


def show_stats():
    wordlist = json.load(open(WORDLIST, encoding="utf-8"))
    cached = sum(1 for w in wordlist if os.path.isdir(os.path.join(ENTRIES_DIR, w["word"])))
    with_paradigm = sum(1 for w in wordlist if w.get("paradigm"))
    with_form = sum(
        1 for w in wordlist
        if w.get("paradigm") and os.path.exists(
            os.path.join(FORMS_DIR, f"{w['paradigm']}_{w['word']}.html")
        )
    )
    print(f"Wordlist:      {len(wordlist)}")
    print(f"Entries cached:{cached}")
    print(f"With paradigm: {with_paradigm}")
    print(f"Forms cached:  {with_form}")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        show_stats()
    elif "--word" in sys.argv:
        idx = sys.argv.index("--word")
        run(target_word=sys.argv[idx + 1])
    else:
        run()
