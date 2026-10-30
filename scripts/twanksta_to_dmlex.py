#!/usr/bin/env python3
"""Konverter: Roh-twanksta-HTML → DMLex-modelliertes NVH (informationsvollständig).

Quelle der Wahrheit ist das ROH-HTML (raw/twanksta/entries/{word}/{lang}.html und
raw/twanksta/forms/{paradigm}_{word}.html), NICHT das verlustbehaftete
parsed/twanksta_entries.json. Die HTML-Parselogik wird aus scripts/twanksta_parse.py
wiederverwendet (import), aber:

  * Homonym-Übersetzungs-Bug behoben: Übersetzungen werden je <li> über den
    natürlichen Schlüssel (word,paradigm,desc) über ALLE 6 Sprachdateien gematcht,
    nicht per erstem word-Treffer (MAPPING §2 Zeile 13).
  * desc als voller innerer HTML-Code (legacy.desc, verbatim) erhalten.
  * descripcio (Artikel) je Entry aus raw neu extrahiert (legacy.article, verbatim).
  * `paradigm` ist eigenes entry-Feld (nicht mehr legacy.paradigm).
  * Belege (Einträge ohne Übersetzung) werden NICHT mehr zu `example` gefaltet
    (Plan fst §0.8): sie bleiben Einträge, wie sie in raw stehen.
  * Die Flexions-Tabellen werden als `inflectedForm`+`tag` ausgeschrieben: jede
    attestierte synthetische Einwort-Zelle, inkl. der vollen Partizip-Deklinations-
    grids (MAPPING §2 Zeile 17 / fst/PLAN_verb_stems.md §0.7). Auch die Nomen- und
    Adjektiv-/Adverb-Tabellen werden emittiert (`sg.<case>`/`pl.<case>` bzw.
    `<g>.<n>.<c>`, `comp.*`, `superl.*`, `adv*`) — je Shape ihr Extraktor.

Dumper, KEINE Morphologie: keine Stamm-/Regel-/Kompositionslogik, kein Import aus
../fst, keine Regularitäts- oder Vollständigkeits-Entscheidung. Perfekt/Futur
werden ausgelassen (rein kompositional, Konstruktion — Plan fst §0.9). Was der
Dumper nicht kennt, überspringt er mit stderr-Warnung (nicht still).

Ausgabe: die FETTE NVH nach parsed/twanksta.nvh (transientes
Zwischenartefakt für den fst-Kompressor; die lean NVH schreibt fst). NVH =
name: value, 2-Space-Einrückung. Mehrzeilige/HTML-Legacy-Werte werden reversibel
einzeilig kodiert (\\n, \\\\), Kanonisierung siehe encode_val/decode_val.

  python3 scripts/twanksta_to_dmlex.py     # schreibt die fette NVH + Statistik
  python3 scripts/twanksta_to_dmlex.py --raw DIR --out FILE --no-save-ids
                                           # nächtlicher Lauf (twanksta_nightly.sh)
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twanksta_parse as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_FILE = os.path.join(ROOT, "parsed", "twanksta.nvh")
# Eingefrorene Entry-ID-Zuordnung (K4): natürlicher Schlüssel → lesbare NCName-ID.
# Einmal beim ersten Vollauf erzeugt, danach append-only (bestehende IDs bleiben
# gepinnt, neue Schlüssel werden angehängt, verschwundene laut gemeldet). Liegt unter
# dem (gitignored) corpus/-Baum; für git-Tracking genügt `!/corpus/ids/` in .gitignore.
IDS_FILE = os.path.join(ROOT, "ids", "twanksta_ids.tsv")
# Hand-kuratierte POS für Fälle, die der desc-Automat nicht abdeckt (K3): v. a.
# Konjunktionen (koord./subord. unterscheidet der desc nicht) und Korrekturen am
# Alt-Gold (Q4: `ikkai`/`anga` subordinierend; indeklinable Lehnnomen → noun). Format:
# `id<TAB>pos` (id = die K4-Entry-ID). Überschreibt pos_of. Inhalt wird lexikografisch
# erarbeitet; fehlt die Datei, bleibt es beim Automaten.
HAND_POS_FILE = os.path.join(ROOT, "ids", "invariable_pos.tsv")

# Site-Sprachcode → normierter IETF-Code (identisch zu dictionary/cmd/migrate langMap).
LANG_CODE = {"engl": "en", "miks": "de", "leit": "lt",
             "latt": "lv", "pols": "pl", "mask": "ru"}


# ── einzeilige, reversible Kodierung für mehrzeilige/HTML-Legacy-Werte ──
def encode_val(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def decode_val(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "r": "\r", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def li_fields(li):
    """Rohfelder eines <li>: word, paradigm, gender, desc(raw inner HTML),
    desc_text(geparst, für Schlüssel+Belege), refs(↑-Eltern), audio, article(raw)."""
    def cls(name):
        el = li.find("span", class_=name) or li.find(class_=name)
        return el
    word_el, numb_el, gend_el = cls("word"), cls("numb"), cls("gend")
    if not word_el:
        return None
    word = word_el.get_text(strip=True)
    paradigm = numb_el.get_text(strip=True) if numb_el else ""
    gender = gend_el.get_text(strip=True) if gend_el else ""
    desc_el = cls("desc")
    desc_raw = desc_el.decode_contents().strip() if desc_el else ""
    # geparster desc-Text + Eltern-Refs (extract_desc_refs mutiert das Element →
    # erst nach der raw-Erfassung aufrufen, auf einer Kopie).
    desc_text, refs = "", []
    if desc_el:
        copy = BeautifulSoup(str(desc_el), "html.parser").find(class_="desc")
        desc_text, refs, _ = tp.extract_desc_refs(copy, word)
    audio_el = li.find("audio")
    audio = audio_el.get("src") if audio_el and audio_el.get("src") else ""
    art_el = cls("descripcio")
    article = art_el.decode_contents().strip() if art_el else ""
    return {"word": word, "paradigm": paradigm, "gender": gender,
            "desc_raw": desc_raw, "desc_text": desc_text, "refs": refs,
            "audio": audio, "article": article}


def key_of(f) -> tuple:
    return (f["word"], f["paradigm"], f["desc_text"])


# ── K4: lesbare, stabile Entry-IDs (NCName) statt `word|paradigm|[desc]` ──
def slugify(word: str) -> str:
    """Headword → NCName-tauglicher ID-Stamm. Leerzeichen → `_`, nur NCName-Zeichen
    (Buchstaben inkl. Diakritika, Ziffern, `.`/`-`/`_`) bleiben, alles andere (`[`,
    `]`, `|`, `:`, …) fällt weg. Diakritika werden NICHT gefaltet (ā≠a) — Faltung
    würde Homographen erzeugen. Beginnt der Slug mit Ziffer/`.`/`-`, kommt `_` davor
    (NCName-Startregel: Buchstabe oder `_`)."""
    s = (word or "").strip().replace(" ", "_")
    slug = "".join(ch for ch in s if ch == "_" or ch == "-" or ch == "." or ch.isalnum())
    if not slug:
        return "_"
    if slug[0].isdigit() or slug[0] in "-.":
        slug = "_" + slug
    return slug


def _natkey(f, idx: int) -> tuple:
    """Eingefrorener Mapping-Schlüssel: natürlicher Schlüssel + Kollisionsindex."""
    return (f["word"], f["paradigm"], f["desc_text"], idx)


def load_id_map() -> dict:
    """Eingefrorene natkey→id-Zuordnung aus IDS_FILE (leer, falls nicht vorhanden)."""
    m: dict = {}
    if not os.path.exists(IDS_FILE):
        return m
    with open(IDS_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            word, par, desc, idx, eid = line.split("\t")
            m[(word, par, decode_val(desc), int(idx))] = eid
    return m


def save_id_map(entries: list) -> None:
    """natkey→id append-only einfrieren: bestehende Zuordnungen bleiben unverändert,
    neue Einträge werden angehängt. So bleibt eine einmal vergebene ID stabil."""
    frozen = load_id_map()
    for e in entries:
        nk = e["_natkey"]
        if nk not in frozen:
            frozen[nk] = e["id"]
    os.makedirs(os.path.dirname(IDS_FILE), exist_ok=True)
    rows = sorted(frozen.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][3]))
    with open(IDS_FILE, "w", encoding="utf-8") as fh:
        fh.write("# word\tparadigm\tdesc_text\tidx\tid  — eingefroren (K4), append-only\n")
        for (word, par, desc, idx), eid in rows:
            fh.write(f"{word}\t{par}\t{encode_val(desc)}\t{idx}\t{eid}\n")


def load_hand_pos() -> dict:
    """Hand-kuratierte id→pos-Überschreibungen (K3); leer, falls keine Datei."""
    m: dict = {}
    if not os.path.exists(HAND_POS_FILE):
        return m
    with open(HAND_POS_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            eid, pos = line.split("\t")[:2]
            m[eid] = pos
    return m


def assign_ids(entries: list, report_drift: bool = False) -> list:
    """Jedem Eintrag eine stabile ID geben. Deterministisch aus dem Headword-Slug;
    Homographen (mehrere Einträge mit gleichem Slug) bekommen `-1`, `-2`, … in
    Eintragsreihenfolge und ein `homograph:`-Feld. Eine eingefrorene IDS_FILE
    überschreibt die berechnete ID (Pinning gegen Parser-Drift); verschwundene
    eingefrorene Schlüssel werden nur beim Vollauf (report_drift) gemeldet — bei
    Teilmengen-Läufen ist Abwesenheit kein Drift."""
    groups: dict = {}
    for e in entries:
        groups.setdefault(slugify(e["word"]), []).append(e)
    for slug, grp in groups.items():
        if len(grp) == 1:
            grp[0]["id"], grp[0]["homograph"] = slug, None
        else:
            for i, e in enumerate(grp, 1):
                e["id"], e["homograph"] = f"{slug}-{i}", i
    frozen = load_id_map()
    if frozen:
        seen = set()
        for e in entries:
            nk = e["_natkey"]
            seen.add(nk)
            if nk in frozen:
                e["id"] = frozen[nk]
                m = re.search(r"-(\d+)$", e["id"])
                e["homograph"] = int(m.group(1)) if m else None
        if report_drift:
            for nk in sorted(set(frozen) - seen):
                print(f"WARNING: eingefrorene ID ohne Eintrag (Parser-Drift?): "
                      f"{nk!r} → {frozen[nk]}", file=sys.stderr)
    hand = load_hand_pos()
    if hand:
        for e in entries:
            if e["id"] in hand:
                e["pos"] = hand[e["id"]]
    return entries


def _li_translations(li):
    trs = []
    tr = li.find(class_="translat")
    if tr:
        for child in tr.find_all(class_="translation-child"):
            num = child.find(class_="translation-number")
            if num:
                num.decompose()
            t = child.get_text(strip=True)
            if t:
                trs.append(t)
    return trs


def lang_li_map(word: str, lang: str) -> dict:
    """(natürlicher Schlüssel, Vorkommen-Index) → Übersetzungsliste, aus
    entries/{word}/{lang}.html. Der Index unterscheidet echte Schlüssel-Kollisionen
    (mehrere <li> mit identischem word|paradigm|desc, z.B. dīnkun ×2) — sonst
    überschreiben sie sich und Übersetzungen gehen verloren. Sprachübergreifend wird
    positionell gematcht (k-tes Vorkommen ↔ k-tes Vorkommen)."""
    path = os.path.join(tp.ENTRIES_DIR, word, f"{lang}.html")
    if not os.path.exists(path):
        return {}
    soup = BeautifulSoup(open(path, encoding="utf-8").read(), "html.parser")
    out, counter = {}, {}
    for li in soup.find_all("li"):
        f = li_fields(li)
        if not f:
            continue
        k = key_of(f)
        idx = counter.get(k, 0)
        counter[k] = idx + 1
        out[(k, idx)] = _li_translations(li)
    return out


def numtype_of(desc_text: str) -> str | None:
    """Nummeraltyp aus dem desc — `crd`/`ord` irgendwo im geparsten desc (nicht nur
    das erste Token: `gen asmas ord`, `aj n (ord)` zählen mit). Feld `numtype:`
    in der NVH; das POS bleibt separat (Q1/K2)."""
    for tok in desc_text.split():
        tok = tok.strip(".,()").lower()
        if tok == "crd":
            return "card"
        if tok == "ord":
            return "ord"
    return None


# desc-Tag → invariable/geschlossene POS (K3). Nur als Fallback, wenn es KEINE
# Formtabelle gibt (flektierende Klassen kommen aus der Shape). Projekteigenes
# POS-Vokabular, 1:1 auf die FST-Tags (adv→+Adv, prep→+Pr, postp→+Po, intj→+Interj,
# part→+Pcle, num→+Num). `encl` setzt KEIN POS (Enklise ist ein Pronomen-Merkmal,
# nicht eine Wortart). Konjunktionen (cconj/sconj) und die ~40 Rest-/Korrekturfälle
# trägt die Hand-Tabelle invariable_pos.tsv (load_hand_pos), nicht dieser Automat.
DESC_POS_TAG = {"av": "adv", "prp": "prep", "psp": "postp", "ij": "intj",
                "pcl": "part", "crd": "num", "ord": "num"}


def pos_of(paradigm: str, word: str, desc_text: str = "") -> str | None:
    """POS aus der Formtabellen-Shape (MAPPING §2 Zeile 6); ohne Tabelle Fallback
    auf den desc-Tag (invariable Klassen, K3). Das POS-Feld bleibt bar
    (noun|adj|verb|adv|prep|postp|intj|part|num); Genus/Nummeraltyp stehen in eigenen
    Feldern. Keine Tabelle und kein erkannter Tag → None (Belege/Skelette ohne POS)."""
    shape, _ = _shape_and_cells(paradigm, word)
    if shape is not None:
        return {"verb": "verb", "adj": "adj", "decl": "noun"}[shape]
    for tok in desc_text.split():
        tok = tok.strip(".,()").lower()
        if tok in DESC_POS_TAG:
            return DESC_POS_TAG[tok]
    return None


def decl_gender(paradigm: str, word: str, gender: str) -> str | None:
    """Genus des Eintrags (masc|fem|neut) — nur für die decl-Shape und nur wenn das
    Roh-<li> ein Genus trägt; sonst None (genuslose Nomen bleiben erlaubt)."""
    shape, _ = _shape_and_cells(paradigm, word)
    if shape == "decl" and gender in GENDERS:
        return gender
    return None


# ── Kanonisches Slot-Vokabular (MAPPING §2 Zeile 17 / §5; fst/PLAN_verb_stems.md
# §0.4 — deckungsgleich mit dem FST-Vokabular, NICHT neu erfunden). ──
# Person/Num sind FUSIONIERT und in Giella-Reihenfolge (sg1 sg2 sp3 pl1 pl2);
# Tempus prs/prt, Partizip prs|prf mit Voice act|pss.
PRONOUN_SLOT = {"as": "sg1", "tū": "sg2", "tāns/tenā/tennan": "sp3",
                "mes": "pl1", "jūs": "pl2", "tenēi/tennas": "sp3"}
INDIC_TENSE_SLOT = {"Present": "prs", "Past": "prt"}
SKIP_TENSE = {"Perfect", "Future"}       # periphrastisch → Konstruktion, kein Datum
IMPERATIVE_SLOT = {"(tū)": "imprt.sg2", "(jūs)": "imprt.pl2"}
# Partizip: (PrcTyp, Voice) — die Voice ist Teil des Slots, nicht eine eigene Ebene.
PART_TYPE_SLOT = {"Present": ("prs", "act"), "Past": ("prf", "act"),
                  "Passive": ("prf", "pss")}
GENDER_SLOT = {"masc": "msc", "fem": "fem", "neut": "neu"}
NUM_SLOT = {"singular": "sg", "plural": "pl"}
CASE_SLOT = {"Nominative": "nom", "Genitive": "gen",
             "Dative": "dat", "Accusative": "acc"}
GENDERS = ("masc", "fem", "neut")
# Grad-Präfix der Adjektiv-Deklination: Positiv trägt kein Präfix (Giella: Nomen
# sg./pl., Adjektiv msc.sg.nom — der Präfix steht, wo ein Grad behauptet wird).
DEGREE_PREFIX = {"declension": "", "comparative": "comp", "superlative": "superl"}
# Adverb-Tabelle: eine Form je Grad, keine Deklination.
ADVERB_SLOT = {"positive": "adv", "comparative": "adv.comp",
               "superlative": "adv.superl"}

_WARNED: set = set()


def _warn_once(kind: str, value, paradigm: str) -> None:
    """Unerwarteten Rohwert melden (einmal je (Art, Wert, Paradigma)) — der Dumper
    überspringt still, aber nie ohne Spur."""
    key = (kind, str(value), paradigm)
    if key in _WARNED:
        return
    _WARNED.add(key)
    print(f"WARNING: unerwarteter {kind} {value!r} (Paradigma {paradigm}) — übersprungen",
          file=sys.stderr)


def _surfaces(cell: str) -> list[str]:
    """Surfaces einer Tabellenzelle: ' / '-Mehrfachformen einzeln, endendes ' si'
    (Reflexivpartikel) abgeschnitten. Sonst nichts — verbatim."""
    out = []
    for part in str(cell).split(" / "):
        part = part.strip()
        if part.endswith(" si"):
            part = part[:-3].strip()
        if part:
            out.append(part)
    return out


def verb_cells(forms: dict, paradigm: str = "") -> list[tuple[str, str]]:
    """Alle synthetischen (surface, tag)-Zellen einer geparsten Formtabelle
    (twanksta_parse.parse_forms), in Tabellenreihenfolge. Reiner Extraktor: jede
    Zelle wird transkribiert, keine Auswahl, keine Komposition.

    Ausgelassen werden nur: Perfekt/Futur (periphrastische Konstruktion) und der
    redundante Partizip-Top-Level-`form` (steckt in full_declension masc/sg/nom).
    Unerwartete tense/type/case-Werte: übersprungen + stderr-Warnung."""
    cells: list[tuple[str, str]] = []
    if not forms:
        return cells
    for group in forms.get("indicative") or []:
        tense = group.get("tense")
        slot = INDIC_TENSE_SLOT.get(tense)
        if slot is None:
            if tense not in SKIP_TENSE:
                _warn_once("indicative-tense", tense, paradigm)
            continue
        for f in group.get("forms") or []:
            person = PRONOUN_SLOT.get(f.get("pronoun"))
            if person is None:
                _warn_once("pronoun", f.get("pronoun"), paradigm)
                continue
            cells += [(s, f"{slot}.{person}") for s in _surfaces(f.get("form") or "")]
    for f in forms.get("subjunctive") or []:
        person = PRONOUN_SLOT.get(f.get("pronoun"))
        if person is None:
            _warn_once("pronoun", f.get("pronoun"), paradigm)
            continue
        cells += [(s, f"subj.{person}") for s in _surfaces(f.get("form") or "")]
    opt = forms.get("optative")
    if isinstance(opt, str):
        cells += [(s, "opt") for s in _surfaces(opt)]
    elif opt is not None:
        _warn_once("optative", type(opt).__name__, paradigm)
    for f in forms.get("imperative") or []:
        slot = IMPERATIVE_SLOT.get(f.get("pronoun"))
        if slot is None:
            _warn_once("imperative-pronoun", f.get("pronoun"), paradigm)
            continue
        cells += [(s, slot) for s in _surfaces(f.get("form") or "")]
    for p in forms.get("participles") or []:
        ptype = PART_TYPE_SLOT.get(p.get("type"))
        if ptype is None:
            _warn_once("participle-type", p.get("type"), paradigm)
            continue
        prctyp, voice = ptype
        for g in p.get("full_declension") or []:
            gender = g.get("gender")
            if gender not in GENDERS:
                _warn_once("participle-gender", gender, paradigm)
                continue
            gender_slot = GENDER_SLOT[gender]
            for c in g.get("cases") or []:
                case = CASE_SLOT.get(c.get("case"))
                if case is None:
                    _warn_once("case", c.get("case"), paradigm)
                    continue
                for num, num_tag in NUM_SLOT.items():
                    cells += [(s, f"part.{prctyp}.{voice}.{gender_slot}.{num_tag}.{case}")
                              for s in _surfaces(c.get(num) or "")]
    return cells


def decl_cells(forms: dict, paradigm: str = "") -> list[tuple[str, str]]:
    """Zellen einer Nomen-Deklination (shape `decl`): je Kasus-Zeile `sg.<case>` und
    `pl.<case>`. Das Genus ist KEIN Slot — es ist Entry-Feld (`gender:`), deshalb
    trägt der Tag es nicht; die Genus-Blöcke werden alle durchlaufen (Einwort-Deklinatio-
    nen haben i.d.R. einen, die adjektivische Kurzform drei).

    Das Genus wird hier bewusst NICHT geprüft: es ist kein Tag-Bestandteil, und
    genuslose Tabellen (Pronomina: `as`, `tū`, `mes`, `jūs` → gender: "") tragen
    vollwertige Zellen. Nur der Kasus wird gemapst — unerwartete Werte: übersprungen
    + stderr-Warnung."""
    cells: list[tuple[str, str]] = []
    if not forms:
        return cells
    for g in forms.get("declension") or []:
        for c in g.get("cases") or []:
            case = CASE_SLOT.get(c.get("case"))
            if case is None:
                _warn_once("case", c.get("case"), paradigm)
                continue
            for num, num_tag in NUM_SLOT.items():
                cells += [(s, f"{num_tag}.{case}") for s in _surfaces(c.get(num) or "")]
    return cells


def adj_cells(forms: dict, paradigm: str = "") -> list[tuple[str, str]]:
    """Zellen der Adjektiv-Tabelle (shape `adj`): die drei Grade über GENDER_SLOT ×
    NUM_SLOT × CASE_SLOT — Positiv `<g>.<n>.<c>`, Komparativ `comp.<g>.<n>.<c>`,
    Superlativ `superl.<g>.<n>.<c>` —, danach die drei Adverbformen `adv`, `adv.comp`,
    `adv.superl`. Wie `verb_cells` ein reiner Extraktor in Tabellenreihenfolge.
    Unerwartete Genus-/Kasus-Werte: Genus-Block bzw. Zelle übersprungen + Warnung."""
    cells: list[tuple[str, str]] = []
    if not forms:
        return cells
    for degree, prefix in DEGREE_PREFIX.items():
        for g in forms.get(degree) or []:
            gender = g.get("gender")
            gender_slot = GENDER_SLOT.get(gender)
            if gender_slot is None:
                _warn_once("adj-gender", gender, paradigm)
                continue
            for c in g.get("cases") or []:
                case = CASE_SLOT.get(c.get("case"))
                if case is None:
                    _warn_once("case", c.get("case"), paradigm)
                    continue
                for num, num_tag in NUM_SLOT.items():
                    tag = ".".join(p for p in (prefix, gender_slot, num_tag, case) if p)
                    cells += [(s, tag) for s in _surfaces(c.get(num) or "")]
    adverb = forms.get("adverb")
    if isinstance(adverb, dict):
        for degree, tag in ADVERB_SLOT.items():
            val = adverb.get(degree)
            if val is None:
                continue
            if not isinstance(val, str):
                _warn_once("adverb-degree", type(val).__name__, paradigm)
                continue
            cells += [(s, tag) for s in _surfaces(val)]
    elif adverb is not None:
        _warn_once("adverb", type(adverb).__name__, paradigm)
    return cells


_FORM_CACHE: dict = {}


def _is_gendered_adj_decl(forms: dict, paradigm: str) -> bool:
    """Adjektivisch flektierte Numeralia/Adjektive ohne Grad-Tabelle (P21–31, 68–70):
    `declension` mit Genusblöcken m/f/n. Sie sind shape `adj` (Genus im Slot,
    `msc.sg.nom`), nicht `decl` — sonst gehen Genus und POS verloren. Pronomina
    (P9–20) haben dieselbe Tabellenform, bleiben aber hier unberührt (eigene POS, Q4)."""
    m = re.match(r"\d+", paradigm or "")
    if not m or int(m.group()) < 21:
        return False
    genders = {g.get("gender") for g in forms.get("declension") or []}
    return len(genders) > 1 and genders <= set(GENDER_SLOT)


def _shape_and_cells(paradigm: str, word: str):
    """(shape, cells) aus forms/{paradigm}_{word}.html; None/[] ohne Tabelle.
    Gecacht: die Tabelle wird pro (paradigm, word) genau einmal gelesen/geparst."""
    key = (paradigm, word)
    hit = _FORM_CACHE.get(key)
    if hit is not None:
        return hit
    forms = {}
    if paradigm:
        fp = os.path.join(tp.FORMS_DIR, f"{paradigm}_{word}.html")
        if os.path.exists(fp):
            forms = tp.parse_forms(open(fp, encoding="utf-8").read())
    shape = None
    if forms:
        if "indicative" in forms or "subjunctive" in forms:
            shape = "verb"
        elif "comparative" in forms or "superlative" in forms or "adverb" in forms:
            shape = "adj"
        elif "declension" in forms:
            shape = "adj" if _is_gendered_adj_decl(forms, paradigm) else "decl"
    # Je Shape ihr Extraktor: Partizip-Grid nur bei Verben, Nomen-/Adjektiv-Zellen
    # nur bei ihren Shapes (eine Adjektiv-Tabelle enthält auch `declension`).
    if shape == "verb":
        cells = verb_cells(forms, paradigm)
    elif shape == "decl":
        cells = decl_cells(forms, paradigm)
    elif shape == "adj":
        cells = adj_cells(forms, paradigm)
    else:
        cells = []
    hit = (shape, cells)
    _FORM_CACHE[key] = hit
    return hit


def inflected_cells(paradigm: str, word: str) -> list[tuple[str, str]]:
    """(surface, tag)-Zellen eines Eintrags (leer ohne Formtabelle)."""
    return _shape_and_cells(paradigm, word)[1]


def entry_cells(entry: dict) -> list[tuple[str, str]]:
    """Zellen eines fertigen Eintrags, dedupliziert: identisches (surface, tag)
    fällt weg, verschiedene Surfaces am selben tag bleiben mehrere inflectedForm."""
    seen, out = set(), []
    for surface, tag in inflected_cells(entry["paradigm"], entry["word"]):
        if (surface, tag) in seen:
            continue
        seen.add((surface, tag))
        out.append((surface, tag))
    return out


def build_entries(words=None):
    """Entry-Menge wie der Alt-Parser (Wortlisten-Loop + Dedup nach natürlichem
    Schlüssel), aber mit korrekt je-Eintrag gematchten Übersetzungen.

    words: optionale Teilmenge von Suchwörtern (für schnelle Tests)."""
    wordlist = json.load(open(tp.WORDLIST, encoding="utf-8"))
    # raw/twanksta/state/wordlist.json (nächtlicher Abgleich) ist eine sortierte
    # Liste von Strings; die Corpus-Wortliste Dicts mit "word"
    wordlist = [w if isinstance(w, dict) else {"word": w} for w in wordlist]
    if words is not None:
        wset = set(words)
        wordlist = [w for w in wordlist if w["word"] in wset]
    # Dict nach natürlichem Schlüssel; bei Duplikaten (dasselbe Lemma taucht auch als
    # Präfix-Treffer in fremden Such-Dirs auf) fehlende reiche Felder NACHFÜLLEN
    # statt den ersten Treffer stumpf zu behalten — sonst gehen Artikel/Audio/
    # Übersetzungen verloren, die nur im exakten Treffer-Dir gerendert werden.
    by_id, order = {}, []
    for item in wordlist:
        word = item["word"]
        engl = os.path.join(tp.ENTRIES_DIR, word, "engl.html")
        if not os.path.exists(engl):
            continue
        maps = {lang: lang_li_map(word, lang) for lang in tp.LANGS}
        soup = BeautifulSoup(open(engl, encoding="utf-8").read(), "html.parser")
        counter = {}
        for li in soup.find_all("li"):
            f = li_fields(li)
            if not f:
                continue
            k = key_of(f)
            idx = counter.get(k, 0)          # Vorkommen-Index für Schlüssel-Kollisionen
            counter[k] = idx + 1
            # Dedup über den natürlichen Schlüssel (+Kollisionsindex); die lesbare ID
            # wird erst nach dem Sammeln vergeben (assign_ids, K4).
            nk = _natkey(f, idx)
            translations = {}
            for lang in tp.LANGS:
                trs = maps[lang].get((k, idx), [])   # positionell, gleicher Index
                if trs:
                    translations[LANG_CODE[lang]] = trs
            if nk not in by_id:
                by_id[nk] = {**f, "_natkey": nk, "translations": translations,
                             "pos": pos_of(f["paradigm"], f["word"], f["desc_text"]),
                             "numtype": numtype_of(f["desc_text"]),
                             "gender": decl_gender(f["paradigm"], f["word"],
                                                   f["gender"])}
                order.append(nk)
            else:
                e = by_id[nk]
                own = (f["word"] == word)   # eigenes Such-Dir ist autoritativ
                for fld in ("audio", "article", "desc_raw"):
                    if not e[fld] and f[fld]:
                        e[fld] = f[fld]
                for lang, trs in translations.items():
                    # eigenes Dir überschreibt (korrekte Sinn-Zuordnung); fremdes Dir
                    # füllt nur fehlende Sprachen (Präfix-Treffer können den Index
                    # anders reihen → sonst falscher Sinn).
                    if trs and (own or lang not in e["translations"]):
                        e["translations"][lang] = trs
    return assign_ids([by_id[k] for k in order], report_drift=(words is None))


import re  # noqa: E402

# twanksta-desc-Siglen (verbatim Label-Tags; Semantik offen, MAPPING §5).
SIGLA = {"MK", "drv", "DRV", "Nx", "Pit", "DIA", "GN", "pra", "Ma", "GlN", "E",
         "VM", "Kr", "alt", "VT", "Gr", "DK", "ON", "MBS", "BPT", "MV", "I", "II"}
# Fundstelle: röm. Buch optional + Seite + optional (Zeile) am Klammer-Ende.
_LOC = re.compile(r"((?:[IVX]+\s+)?\d+(?:\(\d+\))?)\s*$")


def parse_desc(desc_text: str):
    """(grammar, orig_spelling, source_location, sigla[], has_bracket) aus geparstem
    desc. Verlustfrei ist der Rohwert ohnehin in legacy.desc; das hier reichert an."""
    brackets = re.findall(r"\[([^\]]+)\]", desc_text)
    grammar = re.split(r"\[", desc_text, maxsplit=1)[0].strip()
    orig, loc, sigla = "", "", []
    if brackets:
        content = brackets[-1].strip()
        m = _LOC.search(content)
        if m:
            loc = m.group(1).strip()
            content = content[:m.start()].strip()
        toks = content.split()
        kept = []
        for t in toks:
            if t in SIGLA:
                sigla.append(t)
            else:
                kept.append(t)
        orig = " ".join(kept).strip(" ,")
    # Siglen können auch außerhalb der Klammer im grammar-Teil stehen
    for t in re.findall(r"\b(\w+)\b", desc_text):
        if t in SIGLA and t not in sigla:
            sigla.append(t)
    return grammar, orig, loc, sigla, bool(brackets)


def classify_entries(entries):
    """Belege (Einträge ohne Übersetzung) nach MAPPING §3 einordnen. Die S1-Faltung
    zu `example` ist ENTFALLEN (Plan fst §0.8): jeder Beleg bleibt ein eigener Entry
    genau wie im Roh-HTML. Übrig bleiben die Review-Markierungen: aufgelöstes Eltern
    → S3 (Relation + variantSkeleton), fehlendes Eltern → S2 (unresolvedParent),
    elternlos → S5 (orphanAttestation). Rückgabe: (entries, stats)."""
    by_head = {}
    for i, e in enumerate(entries):
        by_head.setdefault(e["word"].lower(), []).append(i)

    def resolve(refs):
        for r in refs:
            cands = by_head.get(r.lower())
            if cands:
                trans = [c for c in cands if entries[c]["translations"]]
                return (trans or cands)[0]
        return None

    # IDs sind bereits in build_entries gesetzt (inkl. #n bei Kollision).
    stats = {"translated": 0, "trans_with_ref": 0, "S2": 0, "S3": 0, "S5": 0}
    out = []
    for e in entries:
        grammar, orig, loc, sigla, has_br = parse_desc(e["desc_text"])
        e["sigla"], e["grammar"], e["orig"], e["loc"] = sigla, grammar, orig, loc
        if e["translations"]:
            stats["translated"] += 1
            pid = resolve(e["refs"])
            if pid is not None:
                e["relation"] = ("attestedFormOf" if grammar else "variantOf",
                                 entries[pid]["id"])
                stats["trans_with_ref"] += 1
            out.append(e)
            continue
        # Beleg (keine Übersetzung)
        pid = resolve(e["refs"]) if e["refs"] else None
        if pid is not None:                               # S3 → Variante
            e["relation"] = ("variantOf", entries[pid]["id"])
            e["review"] = "variantSkeleton"
            stats["S3"] += 1
        elif e["refs"]:                                   # S2 → Eltern fehlt
            e["review"] = "unresolvedParent"
            stats["S2"] += 1
        else:                                             # S5 → elternlos
            e["review"] = "orphanAttestation"
            stats["S5"] += 1
        out.append(e)
    return out, stats


def nvh_lines(entries):
    # FLACHE Top-Level-entry-Knoten (kein lexicographicResource-Wrapper): so
    # erwartet der Fork-Importer (import2dict.py, main_node_name=entry) die Datei —
    # der Wrapper würde als EIN Eintrag interpretieren. Ressourcen-Metadaten
    # (langCode, translationLanguages, Tag-Registries) gehören in die Dict-Config,
    # nicht in die Einträge-Datei.
    L = []
    for e in entries:
        L.append(f'entry: {e["word"]}')
        L.append(f'  id: {e["id"]}')
        if e.get("homograph"):
            L.append(f'  homograph: {e["homograph"]}')
        if e["pos"]:
            L.append(f'  pos: {e["pos"]}')
        if e.get("numtype"):
            L.append(f'  numtype: {e["numtype"]}')
        if e.get("gender"):
            L.append(f'  gender: {e["gender"]}')
        if e["paradigm"]:
            L.append(f'  paradigm: {e["paradigm"]}')
        for s in e.get("sigla", []):
            L.append(f"  label: {s}")
        if e.get("review"):
            L.append(f'  label: {e["review"]}')
        if e["audio"]:
            L.append("  pronunciation:")
            L.append(f'    soundFile: https://wirdeins.twanksta.org{e["audio"]}')
            L.append("    label: deadAudio")
        if e["translations"]:
            L.append("  sense:")
            for lang in tp.LANGS:
                for t in e["translations"].get(LANG_CODE[lang], []):
                    L.append(f"    {LANG_CODE[lang]}: {encode_val(t)}")
        if e.get("relation"):
            rtype, pid = e["relation"]
            L.append(f"  relation: {rtype}")
            role = "form" if rtype == "attestedFormOf" else "variant"
            L.append(f"    member: {e['id']} role: {role}")
            L.append(f"    member: {pid} role: lemma")
        for surface, tag in entry_cells(e):
            L.append(f"  inflectedForm: {surface}")
            L.append(f"    tag: {tag}")
        L.append("  legacy:")
        if e["desc_raw"]:
            L.append(f'    desc: {encode_val(e["desc_raw"])}')
        if e["article"]:
            L.append(f'    article: {encode_val(e["article"])}')
    return L


def _nvh_tree(text):
    """NVH → Liste von (name, value, children)-Knoten (2-Space-Einrückung)."""
    roots, stack = [], []
    for line in text.split("\n"):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        name, _, value = line.strip().partition(": ")
        if not _:  # Zeilen wie "sense:" ohne Wert
            name, value = line.strip().rstrip(":"), ""
        node = {"name": name, "value": value, "children": []}
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def _nvh_dump(nodes, depth=0):
    """Generischer Tree→Text-Dump (für den Serialisierungs-Roundtrip)."""
    out = []
    for n in nodes:
        val = f": {n['value']}" if n["value"] != "" else ":"
        out.append("  " * depth + n["name"] + val)
        out.extend(_nvh_dump(n["children"], depth + 1))
    return out


def parse_nvh(text):
    """Backward: emittiertes NVH → Entry-Dicts (für den Roundtrip-Vergleich)."""
    roots = _nvh_tree(text)
    entries = []
    for en in roots:                     # flache Top-Level-entry-Knoten
        if en["name"] != "entry":
            continue
        e = {"word": en["value"], "id": "", "homograph": None, "pos": None,
             "gender": None, "numtype": "", "labels": [], "translations": {},
             "inflected": [], "relation": None, "paradigm": "", "desc_raw": "",
             "article": "", "audio": "", "examples": []}  # example-Faltung entfernt (Plan fst §0.8)
        for c in en["children"]:
            n, v = c["name"], c["value"]
            if n == "id":
                e["id"] = v
            elif n == "homograph":
                e["homograph"] = int(v) if v.isdigit() else v
            elif n == "pos":
                e["pos"] = v
            elif n == "numtype":
                e["numtype"] = v
            elif n == "gender":
                e["gender"] = v
            elif n == "paradigm":
                e["paradigm"] = v
            elif n == "label":
                e["labels"].append(v)
            elif n == "inflectedForm":
                tag = next((tc["value"] for tc in c["children"] if tc["name"] == "tag"), "")
                e["inflected"].append((c["value"], tag))
            elif n == "pronunciation":
                for pc in c["children"]:
                    if pc["name"] == "soundFile":
                        e["audio"] = pc["value"].replace("https://wirdeins.twanksta.org", "")
            elif n == "sense":
                for sc in c["children"]:
                    if sc["name"] in LANG_CODE.values():
                        e["translations"].setdefault(sc["name"], []).append(decode_val(sc["value"]))
            elif n == "relation":
                e["relation"] = (v, [m["value"] for m in c["children"]
                                     if m["name"] == "member"])
            elif n == "legacy":
                for lc in c["children"]:
                    if lc["name"] == "desc":
                        e["desc_raw"] = decode_val(lc["value"])
                    elif lc["name"] == "article":
                        e["article"] = decode_val(lc["value"])
        entries.append(e)
    return entries


def convert(words=None):
    entries = build_entries(words)
    return classify_entries(entries)


def _paradigm_sort_key(par: str):
    head = re.match(r"\d+", par)
    return (int(head.group()) if head else -1, par)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="raw twanksta HTML → fette NVH")
    ap.add_argument("--raw", help="raw/twanksta-Verzeichnis samt state/wordlist.json "
                    "(Default: corpus/raw/twanksta mit state/twanksta_wordlist.json)")
    ap.add_argument("--out", default=OUT_FILE)
    ap.add_argument("--no-save-ids", action="store_true",
                    help="eingefrorene IDs nur lesen, nicht fortschreiben (ids/ bleibt unverändert)")
    args = ap.parse_args()
    if args.raw:
        tp.ENTRIES_DIR = os.path.join(args.raw, "entries")
        tp.FORMS_DIR = os.path.join(args.raw, "forms")
        tp.WORDLIST = os.path.join(args.raw, "state", "wordlist.json")
    OUT_FILE = args.out
    out, stats = convert()
    if not args.no_save_ids:
        save_id_map(out)   # K4: IDs einfrieren (append-only) nach dem Vollauf
    lines = nvh_lines(out)
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, OUT_FILE)
    print(f"source entries: {len(out)}", file=sys.stderr)
    print(f"  translated: {stats['translated']} (with ref→relation: {stats['trans_with_ref']})",
          file=sys.stderr)
    print(f"  belegs kept as entries: S3={stats['S3']} S2={stats['S2']} S5={stats['S5']}",
          file=sys.stderr)

    # ── Statistik (stdout) ──
    per_paradigm, n_tab, n_cells = collections.Counter(), 0, 0
    for e in out:
        cells = entry_cells(e)
        if not cells:
            continue
        n_tab += 1
        n_cells += len(cells)
        per_paradigm[e["paradigm"]] += len(cells)
    size = os.path.getsize(OUT_FILE)
    print(f"wrote {OUT_FILE}")
    print(f"entries with form table: {n_tab}")
    print(f"inflectedForm total: {n_cells}")
    print(f"file size: {size} bytes ({size / 1e6:.2f} MB)")
    print(f"inflectedForm per paradigm ({len(per_paradigm)} paradigms):")
    for par in sorted(per_paradigm, key=_paradigm_sort_key):
        print(f"  {par:>5}: {per_paradigm[par]:>6}")
