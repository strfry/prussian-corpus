#!/usr/bin/env python3
"""Konverter: Roh-Prusaspira-HTML → DMLex-modelliertes NVH (informationsvollständig).

Eigenständiger Dumper direkt aus dem Roh-HTML. KEINE Kopplung an
`twanksta_to_dmlex.py`, keine gemeinsamen IDs, kein Abgleich mit twanksta (Plan
`prusaspira: eigenständiger Roh-HTML-Parser`). NVH-Writer, ID-Einfrierung und
`encode_val` sind stilistisch identisch, aber eigener Code.

Quelle der Wahrheit: `raw/prusaspira/by_letter/{a..z}.html` (Forward-Index vor
`Na prūsiskan:`; der Rückwärtsindex danach ist redundant) und
`raw/prusaspira/string/{form}_{par}_{typ}.html`.

Entscheidungen sind in `PRUSASPIRA_INVENTORY.md` §8 dokumentiert. Kurz:

  * Nomen: `sg.nom` … `pl.acc` (`pl tantum` → `pl`); Genus → Feld `gender`.
  * Pronomen (Einspaltengitter ohne `<th>`): bloßer Kasus (`nom`/`gen`/`dat`/`acc`).
  * Verb: `prs.<pn>`/`prt.<pn>`/`subj.<pn>` (pn ∈ sg1,sg2,sp3,pl1,pl2; 3sg=3pl=sp3).
    `perfektan`/`perejīngisku` sind periphrastisch → ausgelassen + gezählt.
    `imperatīws` ist mood-aware: Tū→`imprt.sg2`, Jūs→`imprt.pl2`, Mes→`imprt.pl1`,
    As/3sg/3pl→`opt` (die -sei-Form).
  * Partizip: `part.<typ>.<g>.<n>.<case>` (Grid aus `string/`).
  * Adjektiv: `comp.*`/`superl.*` (Grid aus `string/`), Adverb `adv`/`adv.comp`/`adv.superl`.
  * Definit-Gitter (`Prōnominālas fōrmis`): `def.<g>.<n>.<case>` (Prusaspira-Erweiterung,
    nicht im FST-Slot-Inventar). Die Fügungen mit Artikel (`stāi zabāntei`, `stesmu
    zabāntin`) bleiben wörtlich erhalten und tragen `multiword: yes`.
  * Mehrfachformen ` / `/`/` getrennt, endendes ` si` und `!` abgeschnitten.
  * Mehrteilige Oberflächen (Leerzeichen) bleiben wörtlich und werden mit
    `multiword: yes` markiert (formal, keine Morphologie).

Dumper, KEINE Morphologie: jede Zelle wird transkribiert, nichts still verworfen.
Unerwartetes → stderr-Warnung (einmal je Art/Wert) + Zähler.

Ausgabe: `parsed/prusaspira.nvh` + IDS_FILE `ids/prusaspira_ids.tsv`.

  python3 scripts/prusaspira_to_dmlex.py
"""

from __future__ import annotations

import collections
import glob
import html as htmlmod
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LETTER_DIR = os.path.join(ROOT, "raw", "prusaspira", "by_letter")
STRING_DIR = os.path.join(ROOT, "raw", "prusaspira", "string")
OUT_FILE = os.path.join(ROOT, "parsed", "prusaspira.nvh")
IDS_FILE = os.path.join(ROOT, "ids", "prusaspira_ids.tsv")

# ── Label-Mapper (Projekt-Dotted-Slots, MAPPING §2 Z.17; eigener Code) ──
CASE_SLOT = {"Nōm": "nom", "Nom": "nom", "Gēn": "gen", "Gen": "gen",
             "Dāt": "dat", "Dat": "dat", "Akk": "acc"}
GENDER_SLOT = {"m": "msc", "f": "fem", "n": "neu"}
GENDER_FIELD = {"m": "masc", "f": "fem", "n": "neut"}
PERSON_SLOT = {"As": "sg1", "Tū": "sg2", "3sg": "sp3",
               "Mes": "pl1", "Jūs": "pl2", "3pl": "sp3"}
# Nicht-periphrastische Verb-Spalten → Tempus/Modus-Präfix.
VERB_TENSE = {"tēntisku": "prs", "pragūbingisku": "prt", "kōnjunktiws": "subj"}
VERB_SKIP = ("perfektan", "perejīngisku")
VERB_COLS = ("tēntisku", "pragūbingisku", "perfektan", "perejīngisku",
             "imperatīws", "kōnjunktiws", "particīpai")
# ens_str-Typ → Slot-Präfix (Grid aus string/).
REF_PREFIX = {"cp": "comp", "sp": "superl",
              "pcps": "part.prs.act", "pcptac": "part.prf.act",
              "pcptpa": "part.prf.pss"}
ADV_POS = ("adv", "adv.comp", "adv.superl")

DESC_POS = {"AV": "adv", "AJ": "adj", "IJ": "intj", "PRP": "prep",
            "CJ": "conj", "PN": "pron", "CRD": "num", "ORD": "num"}
DESC_PRON_JOBS = ("PN",)
# Kuratierte Sigla-Labels („woher“-Marker; Semantik offen, verbatim). Grammatik-,
# Kasus- und Numerus-Tokens sind KEINE Labels.
SIGLA = {"MK", "DRV", "Nx", "Pit", "DIA", "VM", "GlN", "ON", "DK", "Gr",
         "MBS", "BPT", "TN", "JG", "NO", "Kr", "VT", "GN", "Ma", "MV", "PP",
         "alt", "pra"}

_WARNED: set = set()


def _warn(kind: str, value) -> None:
    key = (kind, str(value))
    if key in _WARNED:
        return
    _WARNED.add(key)
    print(f"WARNING: {kind} {value!r} — übersprungen/unklar", file=sys.stderr)


# ── Block-Extraktion (by_letter) ──
def split_blocks(html: str) -> list[str]:
    """Roh-HTML → `prūsiskai:`-Blöcke (Rückwärtsindex nach `Na prūsiskan:` weg)."""
    cutoff = html.find("Na prūsiskan:")
    if cutoff > 0:
        html = html[:cutoff]
    m = re.search(r'id="rezultatai"', html)
    if not m:
        return []
    return re.split(r"<br\s*/?>\s*prūsiskai:\s*", html[m.start():])


def strip_tags(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s or "").split())


def block_fields(block: str) -> dict | None:
    """word, paradigms, gloss, desc_raw/desc_text, table_raw eines Blocks."""
    wm = re.search(r"class=['\"]wirds['\"]>([^<]+)<", block)
    if not wm:
        return None
    word = htmlmod.unescape(wm.group(1)).strip()
    dnum = re.search(r"class=['\"]dnum['\"]>(.*?)</b>", block, re.S)
    dnum_text = htmlmod.unescape(dnum.group(1)).strip() if dnum else ""
    paradigms = re.findall(r"<([0-9]+[a-z]*)>", dnum_text)
    gloss_m = re.search(r"ēngliskai:\s*<b>(.*?)</b>", block, re.S)
    fonts = re.findall(r"<font[^>]*color=['\"]?#?A0A0A0[^>]*>(.*?)</font>",
                       block, re.S)
    desc_raw = " ".join(f.strip() for f in fonts)
    table_m = re.search(r"<table[^>]*class=['\"]boldtable['\"].*</table>",
                        block, re.S | re.IGNORECASE)
    return {
        "word": word,
        "paradigms": paradigms,
        "gloss": strip_tags(htmlmod.unescape(gloss_m.group(1))) if gloss_m else "",
        "desc_raw": desc_raw,
        "desc_text": strip_tags(htmlmod.unescape(desc_raw)),
        "table_raw": table_m.group(0) if table_m else "",
        "block": block,
    }


# ── Zellen: Oberflächen ──
def surfaces(cell: str) -> list[str]:
    """Surfaces einer Zelle: Tags weg, Entities dekodiert, ` / `/`/` trennen,
    endendes `!` (Interpunktion) und ` si` (Reflexiv) abschneiden."""
    t = htmlmod.unescape(re.sub(r"<[^>]+>", "", cell or ""))
    out = []
    for part in re.split(r"\s*/\s*", t):
        part = " ".join(part.split()).strip()
        if part.endswith("!"):
            part = part[:-1].strip()
        if part.endswith(" si"):
            part = part[:-3].strip()
        if part:
            out.append(part)
    return out


def _multiword(surface: str) -> bool:
    """Formal: eine Oberfläche mit Leerzeichen ist mehrteilig (analytisch, z. B.
    Definit-Fügung `stāi zabāntei`). Keine Morphologie, nur Transparenz."""
    return " " in surface


def _label_of(cell: str) -> str:
    return strip_tags(cell).rstrip(":").strip()


def _tds(html: str) -> list[str]:
    return re.findall(r"<td[^>]*>(.*?)</td>", html or "", re.S)


def _ths(html: str) -> list[str]:
    return [strip_tags(t).rstrip(":").strip()
            for t in re.findall(r"<th[^>]*>(.*?)</th>", html or "", re.S)]


def _ens_refs(html: str) -> list[tuple[str, str, str]]:
    out = []
    for m in re.finditer(r"ens_str\('([^']*)'\)", html or ""):
        f = m.group(1).split(",")
        if len(f) >= 3:
            # Form NICHT strippen: `string/`-Dateinamen kodieren Leerzeichen als
            # `_` (auch ein abschließendes, z. B. `līkints si ` → `līkints_si_`).
            out.append((f[0], f[1].strip(), f[2].strip()))
        else:
            _warn("ens_str", m.group(1))
    return out


# ── Tabellen-Parser ──
def parse_verb_table(table_raw: str) -> tuple[list, list]:
    """(cells, refs) aus einer Verbtabelle. Raster: 1 Label-`<td>` + 7 Wert-`<td>`
    je Person-Zeile (malformed: fehlende `<tr>`)."""
    cols = _ths(table_raw)[1:]                     # führende Leer-<th> weg
    cells: list[tuple[str, str]] = []
    refs: list[tuple[str, str, str]] = []
    tds = _tds(table_raw)
    ncol = len(cols)
    k = 0
    while k < len(tds):
        person = PERSON_SLOT.get(_label_of(tds[k]))
        if person is None:
            k += 1
            continue
        row = tds[k + 1:k + 1 + ncol]
        for name, val in zip(cols, row):
            if name in VERB_TENSE:
                cells += [(s, f"{VERB_TENSE[name]}.{person}") for s in surfaces(val)]
            elif name == "imperatīws":
                if person == "sg2":
                    cells += [(s, "imprt.sg2") for s in surfaces(val)]
                elif person == "pl2":
                    cells += [(s, "imprt.pl2") for s in surfaces(val)]
                elif person == "pl1":
                    # Prusaspira zeigt im imperatīws-Slot für Mes die 1pl-Form;
                    # wörtlich übernehmen (kein stilles Verwerfen).
                    cells += [(s, "imprt.pl1") for s in surfaces(val)]
                else:                                # sg1 / sp3 → Optativ
                    cells += [(s, "opt") for s in surfaces(val)]
            elif name == "particīpai":
                refs += _ens_refs(val)
            elif name in VERB_SKIP:
                pass                                 # periphrastisch
            else:
                _warn("verb-spalte", name)
        k += 1 + ncol
    return cells, refs


def parse_noun_table(table_raw: str) -> tuple[str, list]:
    """(gender, cells) aus einer Nomen-Tabelle (`<th>m</th><th>sg</th><th>pl</th>`)."""
    ths = _ths(table_raw)
    gender, nums = "", []
    for t in ths:
        tl = t.lower()
        if tl in GENDER_SLOT:
            gender = GENDER_FIELD[tl]
        elif "tantum" in tl:
            nums.append("sg" if tl.startswith("sg") else "pl")
        elif tl in ("sg", "pl"):
            nums.append(tl)
    cells = []
    tds = _tds(table_raw)
    k = 0
    while k < len(tds):
        case = CASE_SLOT.get(_label_of(tds[k]))
        if case is None:
            k += 1
            continue
        for num, val in zip(nums, tds[k + 1:k + 1 + len(nums)]):
            cells += [(s, f"{num}.{case}") for s in surfaces(val)]
        k += 1 + len(nums)
    return gender, cells


def parse_pron_table(table_raw: str) -> list:
    """(cells) einer Pronomen-Tabelle ohne `<th>` (bloßer Kasus, kein Numerus)."""
    cells = []
    tds = _tds(table_raw)
    k = 0
    while k < len(tds):
        case = CASE_SLOT.get(_label_of(tds[k]))
        if case is None:
            k += 1
            continue
        if k + 1 < len(tds):
            cells += [(s, case) for s in surfaces(tds[k + 1])]
        k += 2
    return cells


GRID_RE = re.compile(
    r"<table[^>]*>\s*<th[^>]*>\s*</th>((?:\s*<th[^>]*>[^<]*</th>)+)"
    r"\s*<tr[^>]*>(.*?)</table>", re.S)
CAPTION_RE = re.compile(
    r"<caption[^>]*>(?:(?!</caption>).)*Prōnominālas fōrmis.*?</caption>", re.S)


def iter_decl_grids(html: str):
    """Alle Adjektiv-/Partizip-Deklinationsgitter, jeweils als
    ('positive'|'pronominal', match). Das Definit-Gitter folgt seiner Caption."""
    marks = [(m.start(), 0, None) for m in CAPTION_RE.finditer(html)]
    marks += [(m.start(), 1, m) for m in GRID_RE.finditer(html)]
    marks.sort(key=lambda x: x[0])
    pron = False
    for _, kind, m in marks:
        if kind == 0:
            pron = True
        else:
            yield ("pronominal" if pron else "positive", m)
            pron = False


def parse_decl_grid(m) -> list:
    cols = []
    for t in re.findall(r"<th[^>]*>(.*?)</th>", m.group(1), re.S):
        t = strip_tags(t).strip()
        mm = re.match(r"([mfn])\s+(sg|pl)", t)
        if mm:
            cols.append((GENDER_SLOT[mm.group(1)], mm.group(2)))
    cells = []
    tds = _tds(m.group(2))
    k = 0
    while k < len(tds):
        case = CASE_SLOT.get(_label_of(tds[k]))
        if case is None:
            k += 1
            continue
        for (g, n), val in zip(cols, tds[k + 1:k + 1 + len(cols)]):
            cells += [(s, f"{g}.{n}.{case}") for s in surfaces(val)]
        k += 1 + len(cols)
    return cells


def parse_adv_row(html: str) -> list:
    """Die `Adwerban:`-Zeile einer Vergleichs-/Partiziptabelle → `adv*`. 3 Spalten
    (Positiv/Komparativ/Superlativ), leere Zellen entfallen."""
    m = re.search(r"<b>Adwerban:</b>(.*?)(?:</tr>|</table>|$)", html or "", re.S)
    if not m:
        return []
    out = []
    for tag, td in zip(ADV_POS, _tds(m.group(1))):
        out += [(s, tag) for s in surfaces(td)]
    return out


# ── string/-Auflösung ──
_REF_CACHE: dict = {}
USED_REFS: set = set()


def resolve_ref(form: str, par: str, typ: str) -> list:
    """`(form,par,typ)` → Zellen aus `string/{form}_{par}_{typ}.html` (Grid +
    Adwerban-Zeile), mit Slot-Präfix. Fehlt die Datei → Warnung."""
    key = (form, par, typ)
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    prefix = REF_PREFIX.get(typ)
    if prefix is None:
        _warn("ens_str-typ", typ)
        _REF_CACHE[key] = []
        return []
    fn = os.path.join(STRING_DIR, f"{form.replace(' ', '_')}_{par}_{typ}.html")
    cells = []
    if os.path.exists(fn):
        USED_REFS.add(os.path.basename(fn))
        h = open(fn, encoding="utf-8").read()
        for kind, m in iter_decl_grids(h):
            for s, core in parse_decl_grid(m):
                cells.append((s, f"{prefix}.def.{core}"
                              if kind == "pronominal" else f"{prefix}.{core}"))
        for s, pos in parse_adv_row(h):
            cells.append((s, f"{prefix}.{pos}"))
    else:
        _warn("fehlende string-Datei", fn)
    _REF_CACHE[key] = cells
    return cells


# ── Tabellen-Klassifikation eines Blocks ──
def table_kind_cells(f: dict) -> tuple[str, list, list, str]:
    """(kind, cells, refs, gender) eines Blocks."""
    raw = f["table_raw"]
    if not raw:
        return "none", [], [], ""
    ths = _ths(raw)
    if any(t in VERB_COLS for t in ths):
        cells, refs = parse_verb_table(raw)
        return "verb", cells, refs, ""
    if ("Kōmparatiwan" in raw or "Superlatīwan" in raw
            or any(re.match(r"^[mfn]\s+(sg|pl)$", t) for t in ths)):
        cells = []
        for kind, m in iter_decl_grids(f["block"]):
            for s, core in parse_decl_grid(m):
                cells.append((s, f"def.{core}" if kind == "pronominal" else core))
        cells += parse_adv_row(f["block"])
        return "adj", cells, _ens_refs(f["block"]), ""
    if any(t.lower() in ("sg", "pl", "m", "f", "n") or "tantum" in t.lower()
           for t in ths):
        gender, cells = parse_noun_table(raw)
        return "noun", cells, [], gender
    cells = parse_pron_table(raw)
    if cells:
        return "pron", cells, [], ""
    return "other", [], [], ""


# ── desc / POS / Labels ──
def desc_labels(desc_text: str) -> list:
    seen, out = set(), []
    for tok in re.findall(r"[A-Za-zĀĒĪŌŪāēīōū]+", desc_text or ""):
        if tok in SIGLA and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def pos_of(kind: str, desc_text: str) -> str | None:
    toks = [t.strip(".,()").upper() for t in (desc_text or "").split()]
    if kind == "verb":
        return "verb"
    if kind == "pron":
        return "pron"
    pron = any(t in DESC_PRON_JOBS for t in toks)
    if kind == "noun":
        return "pron" if pron else "noun"
    if kind == "adj":
        if pron:
            return "pron"
        for t in toks:
            if t in ("CRD", "ORD"):
                return "num"
        return "adj"
    for t in toks:                                   # keine/andere Tabelle
        if t in DESC_POS:
            return DESC_POS[t]
    return None


# ── Entry-Aufbau ──
def parse_block(block: str) -> dict | None:
    f = block_fields(block)
    if not f:
        return None
    kind, cells, refs, gender = table_kind_cells(f)
    for form, par, typ in refs:
        cells += resolve_ref(form, par, typ)
    seen, dedup = set(), []
    for c in cells:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return {
        "word": f["word"],
        "paradigm": " ".join(f["paradigms"]),
        "gloss": f["gloss"],
        "desc_raw": f["desc_raw"],
        "desc_text": f["desc_text"],
        "kind": kind,
        "pos": pos_of(kind, f["desc_text"]),
        "gender": gender,
        "labels": desc_labels(f["desc_text"]),
        "cells": dedup,
    }


def slugify(word: str) -> str:
    s = (word or "").strip().replace(" ", "_")
    slug = "".join(ch for ch in s
                   if ch in "_-." or unicodedata.normalize("NFC", ch).isalnum())
    if not slug:
        return "_"
    if slug[0].isdigit() or slug[0] in "-.":
        slug = "_" + slug
    return slug


def _natkey(e: dict, idx: int) -> tuple:
    return (e["word"], e["paradigm"], e["desc_text"], idx)


def _encode(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def _decode(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append({"n": "\n", "r": "\r", "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def load_id_map() -> dict:
    m = {}
    if not os.path.exists(IDS_FILE):
        return m
    for line in open(IDS_FILE, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        word, par, desc, idx, eid = line.split("\t")
        m[(word, par, _decode(desc), int(idx))] = eid
    return m


def save_id_map(entries: list) -> None:
    frozen = load_id_map()
    for e in entries:
        nk = e["_natkey"]
        frozen.setdefault(nk, e["id"])
    os.makedirs(os.path.dirname(IDS_FILE), exist_ok=True)
    rows = sorted(frozen.items(),
                  key=lambda kv: (kv[0][0], kv[0][1], kv[0][3]))
    with open(IDS_FILE, "w", encoding="utf-8") as fh:
        fh.write("# word\tparadigm\tdesc_text\tidx\tid  — eingefroren, append-only\n")
        for (word, par, desc, idx), eid in rows:
            fh.write(f"{word}\t{par}\t{_encode(desc)}\t{idx}\t{eid}\n")


def assign_ids(entries: list, report_drift: bool = True) -> list:
    groups: dict = {}
    for e in entries:
        groups.setdefault(slugify(e["word"]), []).append(e)
    for slug, grp in groups.items():
        for i, e in enumerate(grp, 1):
            e["id"] = slug if len(grp) == 1 else f"{slug}-{i}"
            e["homograph"] = None if len(grp) == 1 else i
    seen = set()
    for i, e in enumerate(entries):
        if "_idx" not in e:
            e["_idx"] = i
        e["_natkey"] = _natkey(e, e["_idx"])
    frozen = load_id_map()
    if frozen:
        for e in entries:
            seen.add(e["_natkey"])
            if e["_natkey"] in frozen:
                e["id"] = frozen[e["_natkey"]]
                mm = re.search(r"-(\d+)$", e["id"])
                e["homograph"] = int(mm.group(1)) if mm else None
        if report_drift:
            for nk in sorted(set(frozen) - seen):
                print(f"WARNING: eingefrorene ID ohne Eintrag (Drift?): {nk!r}",
                      file=sys.stderr)
    return entries


def build_entries() -> list:
    entries = []
    for path in sorted(glob.glob(os.path.join(LETTER_DIR, "*.html"))):
        html = open(path, encoding="utf-8").read()
        for block in split_blocks(html):
            e = parse_block(block)
            if e:
                entries.append(e)
    # Natürlicher Schlüssel + Vorkommen-Index (für die eingefrorene ID).
    counter: dict = {}
    for e in entries:
        k = (e["word"], e["paradigm"], e["desc_text"])
        e["_idx"] = counter.get(k, 0)
        counter[k] = e["_idx"] + 1
    for e in entries:
        e["_natkey"] = _natkey(e, e["_idx"])
    return entries


# ── NVH-Writer / -Parser ──
def nvh_lines(entries: list) -> list:
    L = []
    for e in entries:
        L.append(f'entry: {e["word"]}')
        L.append(f'  id: {e["id"]}')
        if e.get("homograph"):
            L.append(f'  homograph: {e["homograph"]}')
        if e["pos"]:
            L.append(f'  pos: {e["pos"]}')
        if e["gender"]:
            L.append(f'  gender: {e["gender"]}')
        if e["paradigm"]:
            L.append(f'  paradigm: {e["paradigm"]}')
        for lab in e["labels"]:
            L.append(f'  label: {lab}')
        if e["gloss"]:
            L.append("  sense:")
            L.append(f'    en: {_encode(e["gloss"])}')
        for surface, tag in e["cells"]:
            L.append(f'  inflectedForm: {surface}')
            L.append(f'    tag: {tag}')
            if _multiword(surface):
                L.append('    multiword: yes')
        L.append("  legacy:")
        L.append(f'    desc: {_encode(e["desc_raw"])}')
    return L


def _nvh_tree(text: str):
    roots, stack = [], []
    for line in text.split("\n"):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        name, _, value = line.strip().partition(": ")
        if not _:
            name, value = line.strip().rstrip(":"), ""
        node = {"name": name, "value": value, "children": []}
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def parse_nvh(text: str) -> list:
    entries = []
    for en in _nvh_tree(text):
        if en["name"] != "entry":
            continue
        e = {"word": en["value"], "id": "", "homograph": None, "pos": None,
             "gender": "", "paradigm": "", "labels": [], "gloss": "",
             "cells": [], "desc_raw": ""}
        for c in en["children"]:
            n, v = c["name"], c["value"]
            if n == "id":
                e["id"] = v
            elif n == "homograph":
                e["homograph"] = int(v) if v.isdigit() else v
            elif n == "pos":
                e["pos"] = v
            elif n == "gender":
                e["gender"] = v
            elif n == "paradigm":
                e["paradigm"] = v
            elif n == "label":
                e["labels"].append(v)
            elif n == "sense":
                e["gloss"] = _decode(next((sc["value"] for sc in c["children"]
                                           if sc["name"] == "en"), ""))
            elif n == "inflectedForm":
                tag = next((tc["value"] for tc in c["children"]
                            if tc["name"] == "tag"), "")
                e["cells"].append((v, tag))
            elif n == "legacy":
                e["desc_raw"] = _decode(next((lc["value"] for lc in c["children"]
                                              if lc["name"] == "desc"), ""))
        entries.append(e)
    return entries


def convert() -> tuple[list, dict]:
    entries = build_entries()
    assign_ids(entries)
    stats = collections.Counter()
    for e in entries:
        stats["entries"] += 1
        stats[f"kind:{e['kind']}"] += 1
        stats["cells"] += len(e["cells"])
        if not e["pos"]:
            stats["pos:empty"] += 1
        if any(_multiword(s) for s, _ in e["cells"]):
            stats["multiword:entries"] += 1
        stats["multiword:cells"] += sum(1 for s, _ in e["cells"] if _multiword(s))
    disk = {os.path.basename(p) for p in glob.glob(os.path.join(STRING_DIR, "*.html"))}
    stats["string:disk"] = len(disk)
    stats["string:used"] = len(USED_REFS)
    stats["string:orphans"] = len(disk - USED_REFS)
    stats["_orphans"] = sorted(disk - USED_REFS)
    return entries, stats


if __name__ == "__main__":
    entries, stats = convert()
    save_id_map(entries)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(nvh_lines(entries)) + "\n")
    size = os.path.getsize(OUT_FILE)
    print(f"wrote {OUT_FILE}")
    print(f"entries: {stats['entries']}")
    for k in ("verb", "adj", "noun", "pron", "other", "none"):
        print(f"  kind {k:>5}: {stats.get('kind:' + k, 0)}")
    print(f"inflectedForm total: {stats['cells']}")
    print(f"pos empty: {stats['pos:empty']}")
    print(f"multiword cells (marked): {stats['multiword:cells']} "
          f"in {stats['multiword:entries']} entries")
    print(f"string files: {stats['string:disk']} (used {stats['string:used']}, "
          f"orphans {stats['string:orphans']})")
    for o in stats["_orphans"]:
        print(f"  orphan: {o}")
    print(f"file size: {size} bytes ({size / 1e6:.2f} MB)")
