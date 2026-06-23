#!/usr/bin/env python3
"""
Parse cached twanksta HTML into a flat JSON list (parsed/twanksta_entries.json).

Input:
  state/twanksta_wordlist.json             — word list with paradigm numbers
  raw/twanksta/entries/{word}/{lang}.html  — search result per word & language
  raw/twanksta/forms/{num}_{word}.html     — paradigm table per lemma

Output:
  parsed/twanksta_entries.json

Schema per entry (the shared dictionary entry schema):
  word, paradigm, gender, desc, audio, translations, forms

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


def _verb_form(text):
    return " ".join(text.split()) or None


def _pcpt_type(stem):
    """Determine participle type from twanksta stem name suffix."""
    s = stem.lower()
    if s.endswith(("ants", "ints", "ents")):
        return "Present"
    if s.endswith(("wuns", "uns")):
        return "Past"
    return "Passive"


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
            val = form_el.get_text(strip=True) if form_el else td.get_text(strip=True)
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
        sub = el.find("table", id="subst")
        if not sub:
            continue
        cases = parse_noun_cases(sub)
        nom_sg = next(
            (c.get("singular", "") for c in cases if c.get("case") == "Nominative"), ""
        )
        if nom_sg:
            participles.append({"type": _pcpt_type(stem), "form": nom_sg})
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

    # Adverb: find the summary table with "Adverb" in hea2 headers
    for table in soup.find_all("table", id="subst"):
        hea2 = [th.get_text(strip=True) for th in table.find_all("th", class_="hea2")]
        if "Adverb" not in hea2:
            continue
        adv_col = hea2.index("Adverb")
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if adv_col < len(cells):
                adv_el = cells[adv_col].find(class_="verb")
                if adv_el:
                    forms["adverb"] = adv_el.get_text(strip=True)
                    break
        break

    return forms


def parse_forms(html):
    """Dispatch to appropriate parser based on form HTML content."""
    if not html or not html.strip():
        return {}
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


def parse_entry_html(html):
    """Extract fields from a single-word search result HTML (one <li>)."""
    soup = BeautifulSoup(html, "html.parser")
    li = soup.find("li")
    if not li:
        return None
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

    return {
        "word": word_el.get_text(strip=True),
        "paradigm": numb_el.get_text(strip=True) if numb_el else "",
        "gender": gend_el.get_text(strip=True) if gend_el else "",
        "desc": desc_el.get_text(strip=True) if desc_el else "",
        "audio": BASE_URL + audio_el["src"] if audio_el and audio_el.get("src") else "",
        "translations": translations,
    }


def parse_word(word, paradigm):
    """Parse all cached files for one word, return entry dict or None."""
    entry_dir = os.path.join(ENTRIES_DIR, word)
    if not os.path.isdir(entry_dir):
        return None
    engl_path = os.path.join(entry_dir, "engl.html")
    if not os.path.exists(engl_path):
        return None

    base = parse_entry_html(open(engl_path, encoding="utf-8").read())
    if not base:
        return None

    translations = {"engl": base.pop("translations")}
    for lang in LANGS[1:]:
        p = os.path.join(entry_dir, f"{lang}.html")
        if not os.path.exists(p):
            continue
        parsed = parse_entry_html(open(p, encoding="utf-8").read())
        if parsed:
            translations[lang] = parsed["translations"]

    forms = {}
    if paradigm:
        form_path = os.path.join(FORMS_DIR, f"{paradigm}_{word}.html")
        if os.path.exists(form_path):
            forms = parse_forms(open(form_path, encoding="utf-8").read())

    return {
        "word": base["word"],
        "paradigm": base["paradigm"],
        "gender": base["gender"],
        "desc": base["desc"],
        "audio": base["audio"],
        "translations": translations,
        "forms": forms,
    }


def run(target_word=None):
    os.makedirs(os.path.join(ROOT, "parsed"), exist_ok=True)
    wordlist = json.load(open(WORDLIST, encoding="utf-8"))
    if target_word:
        wordlist = [w for w in wordlist if w["word"] == target_word]

    entries = []
    skipped = 0
    for item in wordlist:
        word = item["word"]
        paradigm = item.get("paradigm", "")
        entry = parse_word(word, paradigm)
        if entry is None:
            skipped += 1
            continue
        entries.append(entry)

    if target_word:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

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
