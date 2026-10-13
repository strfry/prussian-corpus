"""Tests für den Prusaspira-Roh-HTML→DMLex/NVH-Konverter
(corpus/scripts/prusaspira_to_dmlex.py).

Baumt auf dem GESAMTEN Forward-Index auf (build_entries ≈ 3 s) und prüft:
  (1) Struktur/Abdeckung: wirds-Blöcke == Entries; Typzähler (verb/adj/noun/pron/none).
  (2) Voll-Coverage-Gate: JEDE literale Formzelle (ohne Label, ohne periphrastische
      Perfekt-/Futurzellen, ohne `string/`-Referenzspalten) ist als inflectedForm
      repräsentiert — nichts fällt still weg.
  (3) `string/`-Referenzen (cp/sp/pcps/…): restlos aufgelöst; genau 1 Waise.
  (4) Getreue Transkription an Fixtures (Nomen/Verb/Adjektiv/Pronomen).
  (5) Label-/POS-Mapping, Oberflächen-Cleanup, NVH-Roundtrip.

Entscheidungen siehe PRUSASPIRA_INVENTORY.md §8.
"""
import functools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prusaspira_to_dmlex as C  # noqa: E402

# Fixtures (Forward-Index).
NOUN, NOUN_PAR = "zabanga", "45"
VERB, VERB_PAR = "zabātun", "132"
ADJ, ADJ_PAR = "abipussis", "27"
PRON, PRON_PAR = "as", "1"

VERB_TAG_HEADS = ("prs", "prt", "subj", "opt", "imprt")
DECL_TAG_HEADS = ("sg", "pl")
ADJ_TAG_HEADS = ("msc", "fem", "neu", "comp", "superl", "adv", "def")


@functools.lru_cache(maxsize=1)
def _entries():
    ents, _ = C.convert()
    return ents


@functools.lru_cache(maxsize=1)
def _by_word():
    d = {}
    for e in _entries():
        d.setdefault(e["word"], []).append(e)
    return d


def _entry(word, par=None):
    for e in _by_word()[word]:
        if par is None or e["paradigm"] == par:
            return e
    raise AssertionError(f"{word}/{par} nicht gefunden")


@functools.lru_cache(maxsize=1)
def _raw_blocks():
    """(word, block)-Paare des gesamten Forward-Index, in Reihenfolge."""
    import glob
    out = []
    for path in sorted(glob.glob(os.path.join(C.LETTER_DIR, "*.html"))):
        html = open(path, encoding="utf-8").read()
        for b in C.split_blocks(html):
            f = C.block_fields(b)
            if f:
                out.append((f["word"], b))
    return out


@functools.lru_cache(maxsize=1)
def _nvh_text():
    return "\n".join(C.nvh_lines(_entries()))


def _cells(word, par=None):
    return _entry(word, par)["cells"]


# ── (1) Struktur / Zähler ──
def test_block_and_entry_counts():
    """9966 wirds-Blöcke im Forward-Index ⇒ 9966 Entries (1:1, keine Faltung)."""
    assert len(_raw_blocks()) == 9966
    assert len(_entries()) == 9966


def test_table_kind_counts():
    """Typverteilung aus der Inventur: verb 1967, adj 1205, noun 6214, pron 5, none 575."""
    counts = {}
    for e in _entries():
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    assert counts == {"verb": 1967, "adj": 1205, "noun": 6214,
                      "pron": 5, "none": 575}, counts


# ── (2) Voll-Coverage-Gate ──
def _literal_surfaces(kind, f):
    """Unabhängiges Auslesen der LITERALEN Formzellen eines Blocks (die ohne
    `string/`-Nachschlag im Output stehen müssen). Spiegelbild der Tabellen-
    struktur, bewusst nicht über die Konverter-Funktionen."""
    raw = f["table_raw"]
    if not raw:
        return []
    ths = C._ths(raw)
    tds = C._tds(raw)
    out = []
    if any(t in C.VERB_COLS for t in ths):
        cols = ths[1:]
        ncol = len(cols)
        k = 0
        while k < len(tds):
            person = C.PERSON_SLOT.get(C._label_of(tds[k]))
            if person is None:
                k += 1
                continue
            for name, val in zip(cols, tds[k + 1:k + 1 + ncol]):
                if name in C.VERB_TENSE:
                    out += C.surfaces(val)
                elif name == "imperatīws":
                    out += C.surfaces(val)
                # perfektan / perejīngisku / particīpai → periphrastisch bzw. ref
            k += 1 + ncol
        return out
    if any(re.match(r"^[mfn]\s+(sg|pl)$", t) for t in ths) or "Kōmparatiwan" in raw:
        for m in C.GRID_RE.finditer(f["block"]):
            th = C._ths(m.group(1))
            cols = [1 for t in th if re.match(r"^[mfn]\s+(sg|pl)$", t)]
            if not cols:
                continue
            nc = len(cols)
            gtds = C._tds(m.group(2))
            j = 0
            while j < len(gtds):
                if C.CASE_SLOT.get(C._label_of(gtds[j])) is None:
                    j += 1
                    continue
                for val in gtds[j + 1:j + 1 + nc]:
                    out += C.surfaces(val)
                j += 1 + nc
        m = re.search(r"<b>Adwerban:</b>(.*?)(?:</tr>|</table>|$)", f["block"], re.S)
        if m:
            for td in C._tds(m.group(1)):
                out += C.surfaces(td)
        return out
    if any(t.lower() in ("sg", "pl", "m", "f", "n") or "tantum" in t.lower()
           for t in ths):
        nums = [t for t in ths if t.lower() in ("sg", "pl") or "tantum" in t.lower()]
        k = 0
        while k < len(tds):
            if C.CASE_SLOT.get(C._label_of(tds[k])) is None:
                k += 1
                continue
            for val in tds[k + 1:k + 1 + len(nums)]:
                out += C.surfaces(val)
            k += 1 + len(nums)
        return out
    # Pronomen: je Kasuslabel eine Wertzelle.
    k = 0
    while k < len(tds):
        if C.CASE_SLOT.get(C._label_of(tds[k])) is not None and k + 1 < len(tds):
            out += C.surfaces(tds[k + 1])
            k += 2
        else:
            k += 1
    return out


def test_every_literal_cell_represented():
    """Coverage-Gate über ALLE 9966 Blöcke: jede literale Formzelle ist als
    inflectedForm vorhanden. Block- und Entry-Reihenfolge sind 1:1 (parse_block
    liefert genau für jeden wirds-Block einen Entry)."""
    entries = _entries()
    blocks = _raw_blocks()
    assert len(blocks) == len(entries)
    checked = 0
    for entry, (word, blk) in zip(entries, blocks):
        assert entry["word"] == word
        kind, _, _, _ = C.table_kind_cells(C.block_fields(blk))
        expected = _literal_surfaces(kind, C.block_fields(blk))
        if not expected:
            continue
        checked += 1
        have = {s for s, _ in entry["cells"]}
        missing = [s for s in expected if s not in have]
        assert not missing, (word, kind, missing[:8])
    assert checked > 9000, checked


# ── (3) string/-Referenzen ──
def test_string_refs_all_resolved_and_one_orphan():
    """Alle ens_str-Referenzen des Forward-Index lösen auf; genau eine
    `string/`-Waise (`pilānts_29_pcps.html`, nur im Rückwärtsindex)."""
    import glob
    entries, stats = C.convert()
    assert stats["string:used"] == 7841
    assert stats["string:orphans"] == 1
    orphans = list(stats["_orphans"])
    assert orphans == ["pilānts_29_pcps.html"], orphans
    disk = {os.path.basename(p) for p in
            glob.glob(os.path.join(C.STRING_DIR, "*.html"))}
    assert len(disk) == 7842


def test_ref_forms_keep_trailing_space_for_filename():
    """Formfelder mit endendem Leerzeichen (z. B. `līkints si `) ergeben den
    `_`-kodierten Doppel-Unterstrich-Dateinamen — nicht strippen."""
    part = C.resolve_ref("līkints si ", "29", "pcps")
    assert part and part[0][1].startswith("part.prs.act")
    assert C.resolve_ref("serīpints ", "29", "pcps")
    # Ohne das endende Leerzeichen löste der Name nicht auf.
    assert C.resolve_ref("līkints si", "29", "pcps") == []


# ── (4) Fixtures ──
def test_noun_declension_cells():
    """zabanga/45 (fem): 2 Numeri × 4 Kasus, Reihenfolge sg,pl je Zeile."""
    assert _cells(NOUN, NOUN_PAR) == [
        ("zabanga", "sg.nom"), ("zabangas", "pl.nom"),
        ("zabangas", "sg.gen"), ("zabangan", "pl.gen"),
        ("zabangai", "sg.dat"), ("zabangamans", "pl.dat"),
        ("zabangan", "sg.acc"), ("zabangans", "pl.acc"),
    ]
    e = _entry(NOUN, NOUN_PAR)
    assert e["pos"] == "noun" and e["gender"] == "fem"
    assert {t.split(".")[0] for _, t in e["cells"]} <= set(DECL_TAG_HEADS)


def test_pronoun_bare_case_cells():
    """Pronomen as/1: bloßer Kasus (kein Numerus), Mehrfachformen je Kasus."""
    assert _cells(PRON, PRON_PAR) == [
        ("as", "nom"), ("māise", "gen"),
        ("mennei", "dat"), ("mi", "dat"), ("māim", "dat"),
        ("men", "acc"), ("min", "acc"),
    ]
    e = _entry(PRON, PRON_PAR)
    assert e["kind"] == "pron" and e["pos"] == "pron"


def test_verb_cells_and_periphrastics():
    """zabātun/132: Person×Tempus, Optativ aus der Imperativspalte, Participia aus
    `string/`; Perfekt/Futur (periphrastisch) werden NICHT emittiert."""
    cells = dict((t, s) for s, t in _cells(VERB, VERB_PAR))
    assert cells["prs.sg1"] == "zabāi" and cells["prt.sg1"] == "zabāi"
    assert cells["prs.sp3"] == "zabāi" and cells["prs.pl2"] == "zabāitei"
    assert cells["subj.sg1"] == "zabālai" and cells["subj.pl2"] == "zabālitei"
    assert cells["opt"] == "zabāsei"
    assert cells["imprt.sg2"] == "zabāis" and cells["imprt.pl2"] == "zabāiti"
    # 1pl-Imperativ wird wörtlich übernommen (im Quelle stets = Präsens-1pl).
    assert cells["imprt.pl1"] == "zabāimai"
    assert cells["imprt.pl1"] == cells["prs.pl1"]
    # Participia aller drei Typen (Grid aus string/).
    assert ("zabānts", "part.prs.act.msc.sg.nom") in _cells(VERB, VERB_PAR)
    assert ("zabāwuns", "part.prf.act.msc.sg.nom") in _cells(VERB, VERB_PAR)
    assert ("zabāts", "part.prf.pss.msc.sg.nom") in _cells(VERB, VERB_PAR)
    assert ("zabāntei", "part.prs.act.adv") in _cells(VERB, VERB_PAR)
    # Keine Auxiliar-/Partizip-Konstruktion aus Perfekt/Futur.
    for surface in cells.values():
        assert "asma" not in surface and "wīrst" not in surface, surface
    # Nur Verb-Tag-Köpfe.
    assert {t.split(".")[0] for t in cells} <= set(VERB_TAG_HEADS) | {"part"}


def test_adj_cells_degrees_def_and_adverb():
    """abipussis/27: Positiv `<g>.<n>.<c>`, Definit `def.<g>.<n>.<c>`,
    Komparativ `comp.*`/Superlativ `superl.*` (aus string/), Adverb 3 Grade."""
    cells = _cells(ADJ, ADJ_PAR)
    s = {}
    for surface, tag in cells:
        s.setdefault(tag, []).append(surface)
    assert s["msc.sg.nom"] == ["abipussis"]
    assert s["def.msc.sg.nom"] == ["abipussisis"]
    assert s["comp.msc.sg.nom"] == ["abipuššaisis"]
    assert s["superl.msc.sg.nom"] == ["ukaabipuššaisis", "ukaabipussis"]
    assert s["adv"] == ["abipuššai"]
    assert s["adv.comp"] == ["abipuššais"]
    assert s["adv.superl"] == ["ukaabipuššais", "ukaabipussi"]
    for prefix in ("", "def.", "comp.", "superl."):
        for g in ("msc", "fem", "neu"):
            for n in ("sg", "pl"):
                for c in ("nom", "gen", "dat", "acc"):
                    assert f"{prefix}{g}.{n}.{c}" in s, f"{prefix}{g}.{n}.{c}"
    e = _entry(ADJ, ADJ_PAR)
    assert e["pos"] == "adj" and e["gender"] == ""


# ── (5) Labels / POS / Oberflächen / Roundtrip ──
def test_labels_are_curated_sigla():
    """`label` = kuratierte Quellen-Sigla (verbatim), keine Grammatik-/Kasustokens."""
    assert _entry(VERB, VERB_PAR)["labels"] == ["VM"]
    assert _entry(ADJ, ADJ_PAR)["labels"] == ["MK"]
    assert _entry(NOUN, NOUN_PAR)["labels"] == []
    for e in _entries():
        assert set(e["labels"]) <= C.SIGLA, (e["word"], e["labels"])


def test_pos_mapping():
    assert C.pos_of("verb", "") == "verb"
    assert C.pos_of("noun", "") == "noun"
    assert C.pos_of("adj", "") == "adj"
    assert C.pos_of("pron", "") == "pron"
    assert C.pos_of("adj", "PN 1 NOM SG m") == "pron"
    assert C.pos_of("adj", "AJ n") == "adj"
    assert C.pos_of("none", "AV [schnell MK]") == "adv"
    assert C.pos_of("none", "PRP [über MK]") == "prep"
    assert C.pos_of("none", "GEN ATTR [x MK]") is None
    assert C.pos_of("none", "") is None


def test_surfaces_cleanup():
    """` / `/`/` trennen; endendes ` si` (Reflexiv) und `!` (Imperativ) abtrennen."""
    assert C.surfaces("as / at") == ["as", "at"]
    assert C.surfaces("audedeis/audīs!") == ["audedeis", "audīs"]
    assert C.surfaces("zabāis!") == ["zabāis"]
    assert C.surfaces("blandāi si") == ["blandāi"]
    assert C.surfaces("sergēi si / gaudēi si") == ["sergēi", "gaudēi"]
    assert C.surfaces("") == []


def test_definite_article_phrases_kept_and_marked():
    """Definit-Fügungen mit Artikel bleiben wörtlich erhalten und werden als
    `multiword` markiert (kein stilles Verwerfen). Einzelwort-Definita dagegen
    nicht."""
    assert C._multiword("stāi abipuššai") and not C._multiword("abipussisis")
    cells = _cells(ADJ, ADJ_PAR)
    surfaces = {s for s, _ in cells}
    assert "stāi abipuššai" in surfaces, "Artikel-Fügung fehlt"
    assert ("stesmu abipussin", "def.msc.sg.dat") in cells
    text = _nvh_text()
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if not ln.startswith("  inflectedForm: "):
            continue
        if " " in ln[len("  inflectedForm: "):]:
            assert lines[i + 2] == "    multiword: yes", ln
        else:
            assert lines[i + 2] != "    multiword: yes", ln


def test_homograph_ids():
    """Mehrdeutige Lemmata erhalten Slug-Index-IDs (`aīns-1`/`aīns-2`), der
    Homograph-Index bleibt stabil und eindeutig. `zabeklis` ×2 (gleiches Paradigma
    40, andere Glosse) bleibt als zwei Entries erhalten (Plan-Fixture)."""
    ids = [e["id"] for e in _by_word()["aīns"]]
    assert ids == ["aīns-1", "aīns-2"], ids
    assert [e["homograph"] for e in _by_word()["aīns"]] == [1, 2]
    zb = _by_word()["zabeklis"]
    assert [e["id"] for e in zb] == ["zabeklis-1", "zabeklis-2"]
    assert {e["paradigm"] for e in zb} == {"40"}
    assert {e["gloss"] for e in zb} == {"spring of a lock", "wheel's spring"}
    all_ids = [e["id"] for e in _entries()]
    assert len(all_ids) == len(set(all_ids)), "ID-Kollision"


def test_multi_paradigm_joined_with_space():
    """Phrasen mit mehreren Paradigmen: als ein Feld, space-joined (`69 58`)."""
    e = _entry("Amērikas Peraīnintas Wālstis")
    assert e["paradigm"] == "69 58", e["paradigm"]
    assert e["kind"] == "noun"


def test_roundtrip_nvh_idempotent():
    """NVH-Text → parse_nvh → NVH-Text ist byte-identisch (verlustfrei)."""
    text = _nvh_text()
    assert "\n".join(C.nvh_lines(C.parse_nvh(text))) == text


def test_nvh_structure_has_expected_nodes():
    text = _nvh_text()
    names = set()
    stack = list(C._nvh_tree(text))
    while stack:
        n = stack.pop()
        names.add(n["name"])
        stack.extend(n["children"])
    assert {"entry", "id", "pos", "paradigm", "inflectedForm", "tag",
            "multiword", "sense", "legacy", "desc"} <= names
    assert "example" not in names
