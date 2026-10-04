"""Tests für den HTML→DMLex/NVH-Konverter (corpus/scripts/twanksta_to_dmlex.py).

Laufen auf einem festen Wort-Sample (Sekunden statt Vollauf). Zwei Gates:
  (3a) Coverage: jedes Quell-HTML-Datum ist im Output repräsentiert (gemappt ODER
       in legacy) — nichts fällt still weg.
  (3b) Serialisierungs-Roundtrip: NVH-Text → Tree → Text ist idempotent (verlustfrei).
Plus semantische Checks (Homonym-Fix, Beleg bleibt Entry, Formen-Dump:
paradigm als entry-Feld, inflectedForm+tag, keine examples, kein Perfekt/Futur).
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
    Sinne müssen als #0/#1 erhalten bleiben, keine Übersetzung darf verloren gehen."""
    out = C.build_entries(words=["dīnkun"])
    dk = [e for e in out if e["word"] == "dīnkun"]
    assert len(dk) == 2, [e["id"] for e in dk]
    assert any(e["id"].endswith("#1") for e in dk), [e["id"] for e in dk]
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
    werden als Konstruktion behandelt — fst/PLAN §0.9)."""
    text, _ = _nvh_for(SAMPLE + [VERB])
    assert "example" not in _node_names(text)
    forms = _if_tags(text)
    assert "aukleppa" in forms and forms["aukleppa"] == "prs.sp3"
    for tag in forms.values():
        assert tag.split(".")[0] in ("prs", "prt", "subj", "opt", "imprt", "part"), tag
    # keine Aux+Partizip-Konstruktion aus den Roh-Perfekt-/Futurzellen
    for surface in forms:
        assert not surface.startswith(("asma", "ast ", "assei", "asmai", "astei",
                                       "wīrst", "wīrstei", "wīrstmai")), surface


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
    assert {e["pos"] for e in out if e["pos"]} <= {"noun", "adj", "verb"}


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