"""Tests für den HTML→DMLex/NVH-Konverter (corpus/scripts/twanksta_to_dmlex.py).

Laufen auf einem festen Wort-Sample (Sekunden statt Vollauf). Zwei Gates:
  (3a) Coverage: jedes Quell-HTML-Datum ist im Output repräsentiert (gemappt ODER
       in legacy) — nichts fällt still weg.
  (3b) Serialisierungs-Roundtrip: NVH-Text → Tree → Text ist idempotent (verlustfrei).
Plus semantische Checks (Homonym-Fix, Beleg bleibt Entry, Formen-Dump:
paradigm als entry-Feld, inflectedForm+tag für Verb- UND Nomen-/Adjektiv-Tabellen,
keine examples, kein Perfekt/Futur).
"""
import os
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twanksta_to_dmlex as C  # noqa: E402
import twanksta_parse as tp  # noqa: E402

# Sample: Homonym mit Übersetzungen, Beleg + Elternlemma, Audio+Artikel, simpel.
SAMPLE = ["krikstjāniskan", "galbimai", "Galbtwei", "galbtwei", "Autrīmps",
          "Patrīmps", "Adwēnts", "begalbis", "pagānans", "Pagūns"]

# Fixture-Verb für den Formen-Dump: aukliptun, Par. 97.
VERB = "aukliptun"
VERB_PARADIGM = "97"

# Fixture Nomen (shape decl): krikstjāniskan, Par. 35 (neut).
NOUN = "krikstjāniskan"
NOUN_PARADIGM = "35"
# Genuslose Nomen-Tabelle (Pronomen as, Par. 1): gender: "" darf keine Zellen kosten.
GENDERLESS_NOUN = "as"
GENDERLESS_NOUN_PARADIGM = "1"
# Fixture Adjektiv mit allen drei Graden + Adverb (shape adj): begalbis, Par. 27.
ADJ = "begalbis"
ADJ_PARADIGM = "27"

# Kanonische Tag-Köpfe (Giella, MAPPING §5): Verb, Nomen, Adjektiv/Adverb.
VERB_TAG_HEADS = ("prs", "prt", "subj", "opt", "imprt", "part")
DECL_TAG_HEADS = ("sg", "pl")
ADJ_TAG_HEADS = ("msc", "fem", "neu", "comp", "superl", "adv")
ALL_TAG_HEADS = VERB_TAG_HEADS + DECL_TAG_HEADS + ADJ_TAG_HEADS


def _convert_sample():
    return C.classify_entries(C.build_entries(words=SAMPLE))


def _nvh_for(words):
    out, _ = C.classify_entries(C.build_entries(words=words))
    return "\n".join(C.nvh_lines(out)), out


def _node_names(text):
    """Alle Knotennamen im NVH (rekursiv)."""
    names, stack = set(), list(C._nvh_tree(text))
    while stack:
        n = stack.pop()
        names.add(n["name"])
        stack.extend(n["children"])
    return names


def _if_tags(text):
    """{surface: tag} aus allen inflectedForm-Knoten."""
    out = {}
    for en in C._nvh_tree(text):
        for c in en["children"]:
            if c["name"] == "inflectedForm":
                out[c["value"]] = next(tc["value"] for tc in c["children"]
                                        if tc["name"] == "tag")
    return out


def test_homonym_translations_fixed():
    """krikstjāniskan (p=35) trägt alle 6 Sprachen (Alt-Parser verlor de/lt/lv/pl/ru)."""
    out, _ = _convert_sample()
    hits = [e for e in out if e["word"] == "krikstjāniskan" and e["paradigm"] == "35"]
    assert hits, "translated krikstjāniskan/35 fehlt"
    langs = set(hits[0]["translations"])
    assert langs == {"en", "de", "lt", "lv", "pl", "ru"}, langs


def test_beleg_stays_entry_not_example():
    """galbimai (kein Übersetzung, ↑Galbtwei) bleibt eigener Entry; die S1-Faltung zu
    `example` ist entfernt (fst/PLAN_verb_stems.md §0.8) — im NVH gibt es keine
    example-Knoten mehr."""
    text, out = _nvh_for(SAMPLE)
    hit = next((e for e in out if e["word"] == "galbimai"), None)
    assert hit is not None, "galbimai ging verloren (darf weder Entry noch example sein)"
    assert "example" not in _node_names(text), "example-Knoten dürfen nicht emittiert werden"


def test_coverage_nothing_dropped():
    """Jedes Datum jedes Quell-<li> (word/gender/desc/translations/audio/article) ist
    im Output repräsentiert — als Entry-Feld, Legacy oder inflectedForm."""
    out, _ = _convert_sample()
    words = {e["word"] for e in out}
    descs = {e["desc_raw"] for e in out if e["desc_raw"]}
    audios = {e["audio"] for e in out if e["audio"]}
    arts = {e["article"] for e in out if e["article"]}
    trans = set()
    for e in out:
        for lang, ts in e["translations"].items():
            for t in ts:
                trans.add((lang, t))

    missing = []
    for w in SAMPLE:
        for lang in tp.LANGS:
            p = os.path.join(tp.ENTRIES_DIR, w, f"{lang}.html")
            if not os.path.exists(p):
                continue
            soup = BeautifulSoup(open(p, encoding="utf-8").read(), "html.parser")
            for li in soup.find_all("li"):
                f = C.li_fields(li)
                if not f:
                    continue
                if f["word"] not in words:
                    missing.append(f"word {f['word']!r}")
                if f["desc_raw"] and f["desc_raw"] not in descs:
                    missing.append(f"desc {f['word']!r}: {f['desc_raw']!r}")
                if f["audio"] and f["audio"] not in audios:
                    missing.append(f"audio {f['word']!r}")
                if f["article"] and f["article"] not in arts:
                    missing.append(f"article {f['word']!r}")
                # Übersetzungen dieser Sprache
                tr = li.find(class_="translat")
                if tr:
                    for ch in tr.find_all(class_="translation-child"):
                        num = ch.find(class_="translation-number")
                        if num:
                            num.decompose()
                        t = ch.get_text(strip=True)
                        if t and (C.LANG_CODE[lang], t) not in trans:
                            missing.append(f"trans {f['word']!r}/{lang}: {t!r}")
    assert not missing, "nicht repräsentiert:\n  " + "\n  ".join(sorted(set(missing))[:40])


def test_key_collision_keeps_both_senses():
    """dīnkun hat zwei <li> mit IDENTISCHEM Schlüssel (word|paradigm|desc); beide
    Sinne müssen als Homographen (dīnkun-1/dīnkun-2) erhalten bleiben, keine
    Übersetzung darf verloren gehen (K4: lesbare NCName-IDs statt #n)."""
    out = C.build_entries(words=["dīnkun"])
    dk = [e for e in out if e["word"] == "dīnkun"]
    assert len(dk) == 2, [e["id"] for e in dk]
    assert {e["id"] for e in dk} == {"dīnkun-1", "dīnkun-2"}, [e["id"] for e in dk]
    assert {e["homograph"] for e in dk} == {1, 2}, [e.get("homograph") for e in dk]
    en = {t for e in dk for t in e["translations"].get("en", [])}
    assert "thanks" in en and any("owing" in t for t in en), en
    lt = {t for e in dk for t in e["translations"].get("lt", [])}
    assert {"ačiū", "dėka"} <= lt, lt


def test_paradigm_is_entry_field_not_legacy():
    """paradigm ist eigenes entry-Feld (nicht mehr legacy.paradigm)."""
    text, out = _nvh_for([VERB])
    entry = next(e for e in out if e["word"] == VERB and e["paradigm"] == VERB_PARADIGM)
    lines = text.split("\n")
    i = lines.index(f"entry: {entry['word']}")
    block = []
    for line in lines[i + 1:]:
        if not line.startswith(" "):
            break
        block.append(line)
    assert f"  paradigm: {VERB_PARADIGM}" in block, block[:6]
    assert not any(l.strip().startswith("paradigm:") for l in block
                   if l.startswith("    ")), "paradigm noch unter legacy"
    back = {e["id"]: e for e in C.parse_nvh(text)}[entry["id"]]
    assert back["paradigm"] == VERB_PARADIGM


def test_verb_inflected_forms_transcribed():
    """aukliptun/97: Präsens-/Präteritum-p3, Partizip-Grid und Stimmungen als
    inflectedForm+tag (kanonisches Slot-Vokabular = Giella: Person/Num fusioniert
    sg1 sg2 sp3 pl1 pl2, prs/prt, imprt.sg2/pl2, part.<prctyp>.<voice>.<msc|fem|neu>)."""
    text, out = _nvh_for([VERB])
    entry = next(e for e in out if e["word"] == VERB and e["paradigm"] == VERB_PARADIGM)
    cells = C.entry_cells(entry)
    assert ("aukleppa", "prs.sp3") in cells, cells[:8]
    assert ("auklippa", "prt.sp3") in cells
    assert ("aukliplai", "subj.sp3") in cells
    assert ("auklipsei", "opt") in cells
    assert ("aukleppais", "imprt.sg2") in cells and ("aukleppaiti", "imprt.pl2") in cells
    # volles Partizip-Grid (3 Typen × 3 Genera × 2 Numeri × 4 Kasus = 72 Zellen)
    part = {t: s for s, t in cells if t.startswith("part.")}
    assert len(part) == 72, sorted(part)
    assert part["part.prs.act.msc.sg.nom"] == "auklepānts"
    for tag in ("part.prs.act.msc.sg.nom", "part.prf.act.neu.pl.acc",
                "part.prf.pss.fem.pl.dat"):
        assert tag in part, tag
    # Zellen sind dedupliziert; verschiedene Surfaces am selben tag bleiben
    assert len(cells) == len(set(cells))
    # und genau so im NVH
    forms = _if_tags(text)
    assert forms["aukleppa"] == "prs.sp3" and forms["auklippa"] == "prt.sp3"
    assert forms["auklepānts"] == "part.prs.act.msc.sg.nom"


def test_no_example_nodes_and_no_perfect_future_tags():
    """Keine `example`-Knoten; keine prs/prt-Tags aus Perfekt/Futur (periphrastisch,
    werden als Konstruktion behandelt — fst/PLAN §0.9). Alle emittierten Tags stehen
    im kanonischen Vokabular (Verb ∪ Nomen ∪ Adjektiv/Adverb)."""
    text, _ = _nvh_for(SAMPLE + [VERB, NOUN, ADJ])
    assert "example" not in _node_names(text)
    forms = _if_tags(text)
    assert "aukleppa" in forms and forms["aukleppa"] == "prs.sp3"
    for tag in forms.values():
        assert tag.split(".")[0] in ALL_TAG_HEADS, tag
    # keine Aux+Partizip-Konstruktion aus den Roh-Perfekt-/Futurzellen
    for surface in forms:
        assert not surface.startswith(("asma", "ast ", "assei", "asmai", "astei",
                                       "wīrst", "wīrstei", "wīrstmai")), surface
    # Der Verb-Pfad emittiert ausschließlich Verb-Tags: die Nomen-/Adjektiv-Zellen
    # dürfen nicht in die Partizip-Deklination durchschlagen (Formtabelle eines
    # Verbs trägt `participles`, nicht `declension`).
    assert {t.split(".")[0] for _, t in C.inflected_cells(VERB_PARADIGM, VERB)} \
        <= set(VERB_TAG_HEADS)


def test_numtype_from_desc_anywhere():
    """K2: `crd`/`ord` irgendwo im desc → Eintragsfeld `numtype: card|ord` (nicht nur
    als erstes Token: `gen asmas ord` zählt), plus Roundtrip über parse_nvh."""
    assert C.numtype_of("ord [aīnadesimt MK]") == "ord"
    assert C.numtype_of("gen asmas ord [Asmus 33]") == "ord"
    assert C.numtype_of("crd [nō + jedenaście MK]") == "card"
    assert C.numtype_of("aj n [mk]") is None
    assert C.numtype_of("") is None
    text, out = _nvh_for(["ainadesīmts"])
    entry = next(e for e in out if e["word"] == "ainadesīmts")
    assert entry["numtype"] == "ord"
    assert f"  numtype: {entry['numtype']}" in text
    back = {e["id"]: e for e in C.parse_nvh(text)}[entry["id"]]
    assert back["numtype"] == "ord"


def test_noun_declension_cells():
    """krikstjāniskan/35 (shape decl): jede Kasus-Zeile → `sg.<case>` + `pl.<case>`,
    in Tabellenreihenfolge. Kein Genus im Key (es ist Entry-Feld `gender:`), und
    POS/Genus-Kontrakt bleibt unberührt."""
    text, out = _nvh_for([NOUN])
    entry = next(e for e in out if e["word"] == NOUN and e["paradigm"] == NOUN_PARADIGM)
    cells = C.entry_cells(entry)
    assert cells == [
        (NOUN, "sg.nom"), ("krikstjaniskāi", "pl.nom"),
        ("krikstjāniskas", "sg.gen"), (NOUN, "pl.gen"),
        ("krikstjānisku", "sg.dat"), ("krikstjaniskammans", "pl.dat"),
        (NOUN, "sg.acc"), ("krikstjāniskans", "pl.acc"),
    ], cells
    assert {t.split(".")[0] for _, t in cells} <= set(DECL_TAG_HEADS)
    # Genus nur im Entry-Feld, nicht im Tag
    assert entry["pos"] == "noun" and entry["gender"] == "neut"
    assert not any(t.split(".")[0] in ADJ_TAG_HEADS for _, t in cells)
    # eins zu eins im NVH
    forms = _if_tags(text)
    assert forms["krikstjaniskāi"] == "pl.nom" and forms["krikstjāniskas"] == "sg.gen"


def test_genderless_noun_keeps_cells():
    """Pronomen as (gender: "") trägt vollwertige Zellen — das Genus ist beim Nomen kein
    Tag-Bestandteil und darf den Extraktor nicht blockieren."""
    path = os.path.join(tp.FORMS_DIR, f"{GENDERLESS_NOUN_PARADIGM}_{GENDERLESS_NOUN}.html")
    with open(path, encoding="utf-8") as f:
        cells = C.decl_cells(tp.parse_forms(f.read()))
    assert cells == [
        ("as", "sg.nom"), ("mes", "pl.nom"),
        ("māise", "sg.gen"), ("nūse", "pl.gen"),
        ("mennei", "sg.dat"), ("nūmans", "pl.dat"),
        ("men", "sg.acc"), ("mans", "pl.acc"),
    ], cells


def test_adj_cells_degrees_and_adverb():
    """begalbis/27 (shape adj): Positiv `<g>.<n>.<c>`, Komparativ `comp.<g>.<n>.<c>`,
    Superlativ `superl.<g>.<n>.<c>`, Adverb `adv`/`adv.comp`/`adv.superl` —
    3×3×2×4 + 3 = 75 Zellen, Reihenfolge Genus→Numerus→Kasus."""
    text, out = _nvh_for([ADJ])
    entry = next(e for e in out if e["word"] == ADJ and e["paradigm"] == ADJ_PARADIGM)
    cells = C.entry_cells(entry)
    by_tag = {}
    for surface, tag in cells:
        by_tag.setdefault(tag, []).append(surface)
    assert len(cells) == 75, len(cells)
    assert by_tag["msc.sg.nom"] == ["begalbis"] and by_tag["fem.sg.nom"] == ["begalbi"]
    assert by_tag["neu.sg.nom"] == ["begalbi"]
    assert by_tag["comp.msc.sg.nom"] == ["begalbjaisis"]
    assert by_tag["superl.msc.sg.nom"] == ["ukabegalbjaisis"]
    assert by_tag["comp.neu.pl.acc"] == ["begalbjaisins"]
    assert by_tag["adv"] == ["begalbjai"]
    assert by_tag["adv.comp"] == ["begalbjais"]
    assert by_tag["adv.superl"] == ["ukabegalbjais"]
    # vollständiges Grid: jeder Grad trägt alle 3 Genus × 2 Numeri × 4 Kasus
    for prefix in ("", "comp.", "superl."):
        for g in ("msc", "fem", "neu"):
            for n in ("sg", "pl"):
                for c in ("nom", "gen", "dat", "acc"):
                    assert f"{prefix}{g}.{n}.{c}" in by_tag, f"{prefix}{g}.{n}.{c}"
    # Positiv ohne Grad-Präfix, POS bleibt bar adj, kein Genus-Feld
    assert entry["pos"] == "adj" and entry["gender"] is None
    forms = _if_tags(text)
    assert forms["begalbjais"] == "adv.comp" and forms["ukabegalbjais"] == "adv.superl"


def test_decl_cells_split_surfaces_and_skip_unknown_case(capsys):
    """Nomen-Zellen: ' / '-Mehrfachformen je eine inflectedForm, Tabellenreihenfolge
    singular→plural je Zeile. Unerwarteter Kasus: übersprungen + stderr-Warnung."""
    forms = {"declension": [{"gender": "neut", "cases": [
        {"case": "Nominative", "singular": "as / mes", "plural": "as"},
        {"case": "Vocative", "singular": "z"},
    ]}]}
    assert C.decl_cells(forms, "998") == [("as", "sg.nom"), ("mes", "sg.nom"),
                                          ("as", "pl.nom")]
    err = capsys.readouterr().err
    assert "Vocative" in err, err
    # Genus wird nicht geprüft (kein Tag-Bestandteil): '' und 'm' ändern nichts
    for gender in ("", "m", "masc"):
        assert C.decl_cells({"declension": [{"gender": gender, "cases": [
            {"case": "Dative", "singular": "q"}]}]}, "997") == [("q", "sg.dat")]


def test_adj_cells_unknown_gender_skipped_and_warned(capsys):
    """Adjektiv-Zellen: unbekanntes Genus → ganzer Genus-Block übersprungen + Warnung;
    unerwarteter adverb-Werttyp: übersprungen + Warnung. Die Gültigkeit des Genus
    garantiert `test_adj_gender_vocabulary_matches_noun_tables`."""
    good = {"gender": "masc", "cases": [{"case": "Nominative", "singular": "b"}]}
    forms = {"declension": [good, {"gender": "x", "cases": [
        {"case": "Nominative", "singular": "qu"}]}],
             "adverb": {"positive": "bai", "comparative": 7}}
    assert C.adj_cells(forms, "996") == [("b", "msc.sg.nom"), ("bai", "adv")]
    err = capsys.readouterr().err
    assert "adj-gender 'x'" in err and "adverb-degree" in err, err
    assert C.adj_cells({"adverb": "bais"}, "995") == []
    assert "adverb" in capsys.readouterr().err


def test_adj_gender_vocabulary_matches_noun_tables():
    """Nomen- und Adjektiv-Tabelle liefern dasselbe Genus-Vokabular (masc/fem/neut) —
    der Konverter braucht dafür keine Alias-Tabelle, sondern GENDER_SLOT direkt."""
    with open(os.path.join(tp.FORMS_DIR, f"{ADJ_PARADIGM}_{ADJ}.html"),
              encoding="utf-8") as f:
        forms = tp.parse_forms(f.read())
    for degree in ("declension", "comparative", "superlative"):
        genders = [g["gender"] for g in forms[degree]]
        assert genders == ["masc", "fem", "neut"], (degree, genders)
        assert set(genders) <= set(C.GENDER_SLOT)


def test_pos_is_bare_and_gender_is_own_field():
    """POS-Kontrakt: `pos` ist bar (noun|adj|verb), das Genus steht im eigenen
    `gender`-Feld — kein `pos: noun-masc` mehr. Genuslose Nomen bleiben erlaubt
    (dann fehlt die gender-Zeile)."""
    text, out = _nvh_for(SAMPLE + ["abbai"])
    assert not any(l.strip().startswith("pos: noun-") for l in text.split("\n"))
    # Knoten-Ebene: gender steht unmittelbar nach pos, nur bei Nomen
    by_id = {}
    for en in C._nvh_tree(text):
        ids = [c["value"] for c in en["children"] if c["name"] == "id"]
        if ids:
            by_id[ids[0]] = [c["name"] for c in en["children"]]
    for e in out:
        children = by_id[e["id"]]
        if "pos" not in children:
            continue
        pos_i = children.index("pos")
        assert ("gender" in children) == (e["gender"] is not None), e
        if e["gender"]:
            assert children[pos_i + 1] == "gender", children[:4]
            assert e["gender"] in C.GENDERS, e
    # Stichprobe: Nomen mit Genus tragen gender, genuslose Nomen nicht
    assert next(e for e in out if e["word"] == "Adwēnts")["gender"] == "masc"
    assert next(e for e in out if e["word"] == "krikstjāniskan"
                and e["paradigm"] == "35")["gender"] == "neut"
    assert next(e for e in out if e["word"] == "abbai")["gender"] is None
    # Verben/Adjektive tragen kein Genus, POS bleibt bar
    assert next(e for e in out if e["word"] == "galbtwei")["pos"] == "verb"
    assert all(e["gender"] is None for e in out if e["pos"] in ("verb", "adj"))
    # POS bleibt bar; flektierende Klassen aus der Shape, invariable aus dem desc (K3)
    # bzw. der Hand-Tabelle (cconj/sconj/pron). Volles projekteigenes POS-Vokabular:
    assert {e["pos"] for e in out if e["pos"]} <= {
        "noun", "adj", "verb", "adv", "prep", "postp", "intj", "part", "num",
        "cconj", "sconj", "pron"}


def test_pos_desc_fallback_for_invariables():
    """Ohne Formtabelle (leeres Paradigma) leitet pos_of die invariable Klasse aus
    dem desc-Tag ab (K3): av→adv, prp→prep, psp→postp, ij→intj, pcl→part, crd/ord→num;
    `encl` und ein tagloser desc → None."""
    assert C.pos_of("", "x", "prp [über MK]") == "prep"
    assert C.pos_of("", "x", "av [schnell]") == "adv"
    assert C.pos_of("", "x", "psp paggan") == "postp"
    assert C.pos_of("", "x", "ij") == "intj"
    assert C.pos_of("", "x", "pcl") == "part"
    assert C.pos_of("", "x", "aj n (av)") == "adv"
    assert C.pos_of("", "x", "encl") is None
    assert C.pos_of("", "x", "n MK") is None


def test_parse_nvh_recovers_gender():
    """Das neue gender-Feld überlebt den NVH-Roundtrip (Backward-Reader)."""
    out, _ = _convert_sample()
    text = "\n".join(C.nvh_lines(out))
    back = {e["id"]: e for e in C.parse_nvh(text)}
    for e in out:
        if e["pos"] == "noun":
            assert back[e["id"]]["gender"] == e["gender"], e["id"]


def test_surfaces_split_and_reflexive_strip():
    """' / '-Mehrfachformen werden je eine inflectedForm; ein endendes ' si'
    (Reflexivpartikel) wird abgeschnitten. Sonst nichts — verbatim."""
    assert C._surfaces("as / at") == ["as", "at"]
    assert C._surfaces("blandāi si") == ["blandāi"]
    assert C._surfaces("sergēi si / gaudēi si") == ["sergēi", "gaudēi"]
    assert C._surfaces("") == []


def test_perfect_future_skipped_quietly_unknown_values_warn(capsys):
    """Perfekt/Futur werden ausgelassen (periphrastische Konstruktion) — ohne Warnung.
    Unerwartete tense/type/case-Werte: übersprungen, aber auf stderr gemeldet."""
    forms = {
        "indicative": [
            {"tense": "Present", "forms": [{"pronoun": "as", "form": "aukleppa"}]},
            {"tense": "Past", "forms": [{"pronoun": "tāns/tenā/tennan", "form": "auklippa"}]},
            {"tense": "Perfect", "forms": [{"pronoun": "as", "form": "asma auklippuns"}]},
            {"tense": "Future", "forms": [{"pronoun": "as", "form": "wīrst auklippuns"}]},
            {"tense": "Habitual", "forms": [{"pronoun": "as", "form": "pflegt"}]},
        ],
        "participles": [{"type": "Wackel", "form": "z", "full_declension": []},
                        {"type": "Past", "form": "y", "full_declension": [
                            {"gender": "masc", "cases": [{"case": "Vocative", "singular": "q"}]}]}],
    }
    assert C.verb_cells(forms, "999") == [("aukleppa", "prs.sg1"), ("auklippa", "prt.sp3")]
    err = capsys.readouterr().err
    assert "Habitual" in err and "Wackel" in err and "Vocative" in err, err
    assert "Perfect" not in err and "Future" not in err, err


def test_serialization_roundtrip_idempotent():
    """NVH-Text → Tree → Text ist byte-identisch (Serialisierung verlustfrei)."""
    out, _ = _convert_sample()
    text = "\n".join(C.nvh_lines(out))
    tree = C._nvh_tree(text)
    text2 = "\n".join(C._nvh_dump(tree))
    assert text == text2


def test_parse_nvh_recovers_translations_legacy_and_forms():
    """Backward parse_nvh gewinnt Übersetzungen, legacy.desc und die Formen verlustfrei
    zurück."""
    out, _ = _convert_sample()
    text = "\n".join(C.nvh_lines(out))
    back = {e["id"]: e for e in C.parse_nvh(text)}
    src = next(e for e in out if e["word"] == "krikstjāniskan" and e["paradigm"] == "35")
    b = back[src["id"]]
    assert b["translations"] == src["translations"], (b["translations"], src["translations"])
    assert b["desc_raw"] == src["desc_raw"]
    for e in out:
        assert back[e["id"]]["inflected"] == C.entry_cells(e), e["id"]