"""Tests für den nächtlichen Twanksta-Abgleich (corpus/scripts/twanksta_nightly.py).

Offline, auf kleinen HTML-Schnipseln im Format von /search/. Geprüft werden die
Stellen, an denen ein Fehler still Änderungen verschlucken würde: Cap-Erkennung
des Präfix-Sweeps, Homonym-Signatur, Formtabellen-Schlüssel, Löschregel.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twanksta_nightly as N  # noqa: E402


def li(word, numb=None, desc="", tr="x"):
    numb_html = f" <span class='numb'>{numb}</span>" if numb else ""
    return (f"<li><span class='word'>{word}</span>{numb_html}  <span class='desc'>{desc}</span>"
            f"<span class=\"translat\"><span class='translation-child'>"
            f"<span class='translation-number'>1</span> {tr}</span></span></li>")


def page(*lis):
    return "﻿   <ul>" + "".join(lis) + "</ul>"


def test_norm_matches_site_insensitivity():
    assert N.norm("Dēiws") == N.norm("deiws") == "deiws"
    assert N.norm("šlāuksnaszaks").startswith("slauk")


def test_is_complete_counts_only_prefix_hits():
    # 9 Präfix-Treffer + beliebig viele Übersetzungs-/Teilstring-Treffer: vollständig
    hits = [li(f"ab{i}") for i in range(9)] + [li(f"x{i}ab") for i in range(30)]
    assert N.is_complete(N.li_blocks(page(*hits)), "ab")
    # 10 Präfix-Treffer: Cap erreicht, muss gesplittet werden
    hits.append(li("abz"))
    assert not N.is_complete(N.li_blocks(page(*hits)), "ab")


def test_is_complete_uses_site_normalization():
    hits = [li(f"Āb{i}") for i in range(10)]
    assert not N.is_complete(N.li_blocks(page(*hits)), "ab")


def test_li_signature_order_independent_and_homonym_complete():
    a, b = li("spārts", "60", "N"), li("spārts", "26", "aj")
    other = li("spārtiskan")
    assert N.li_signature(N.li_blocks(page(a, other, b)), "spārts") == \
        N.li_signature(N.li_blocks(page(b, a)), "spārts")
    # ein fehlendes Homonym ist eine Änderung
    assert N.li_signature(N.li_blocks(page(a)), "spārts") != \
        N.li_signature(N.li_blocks(page(a, b)), "spārts")


def test_li_signature_detects_desc_edit():
    old = page(li("abbejan", desc='↑ <a href="#abbai">Abbai</a> dat'))
    new = page(li("abbejan", desc='↑ <a href="#abbai">Abbai</a> gen'))
    assert N.li_signature(N.li_blocks(old), "abbejan") != N.li_signature(N.li_blocks(new), "abbejan")


def test_form_keys_only_own_entries_with_paradigm():
    html = page(li("as", "1"), li("as"), li("asman", "35"))
    assert N.form_keys(html, "as") == {("1", "")}


def test_follow_chars_excludes_wildcards_and_allows_space():
    f = N.follow_chars(["ab_c", "Ād"])
    assert f[""] == {"a"}
    assert "_" not in f["b"] and " " in f["a"] and "d" in f["a"]


class _Store:
    def read(self, path):
        return None

    def entry_path(self, w, lang):
        return f"entries/{w}/{lang}.html"

    def form_files(self, w):
        return [f"forms/1_{w}.html"]


def _run(empty, words=None, new=()):
    r = N.Run.__new__(N.Run)
    r.store, r.empty, r.deletes, r.writes = _Store(), set(empty), set(), {}
    r.words, r.new = sorted(set(words if words is not None else empty)), set(new)
    return r


def test_deletion_needs_two_consecutive_empty_checks():
    r = _run({"a", "b"})
    gone, pending, _ = r.resolve_deletions({"a": "2026-10-20", "c": "2026-10-20"}, "2026-10-28", False)
    assert gone == {"a"}
    assert pending == {"b": {"since": "2026-10-28"}}   # c antwortet wieder → nicht mehr pending
    assert "entries/a/engl.html" in r.deletes and "forms/1_a.html" in r.deletes
    assert not any("/b/" in p for p in r.deletes)


def test_partial_run_never_deletes():
    r = _run({"a"})
    gone, pending, _ = r.resolve_deletions({"a": "2026-10-20"}, "2026-10-28", True)
    assert gone == set() and pending == {"a": "2026-10-20"} and not r.deletes


def test_deletion_spares_directory_shared_with_live_lemma():
    # Juli-Wortliste hatte "ēit_", die Seite heißt "ēit?": gleicher Ordner auf der Platte.
    r = _run({"Kāigi tebbei ēit_"}, words=["Kāigi tebbei ēit_"], new={"Kāigi tebbei ēit?"})
    gone, _, _ = r.resolve_deletions({"Kāigi tebbei ēit_": "2026-10-28"}, "2026-10-29", False)
    assert gone == {"Kāigi tebbei ēit_"}      # aus der Wortliste raus …
    assert not r.deletes                       # … aber die Dateien bleiben


class _FakeClient:
    def __init__(self, content):
        self.content = content

    async def search(self, s, lang):
        return self.content


class _FileStore(_Store):
    def __init__(self, stored):
        self.stored = stored

    def read(self, path):
        return self.stored


def _check(stored, live):
    import asyncio
    r = N.Run.__new__(N.Run)
    r.store, r.client, r.lang = _FileStore(stored.encode()), _FakeClient(live.encode()), "engl"
    r.empty, r.failed, r.marked = set(), set(), []

    async def mark(word, is_new):
        r.marked.append(word)
    r.mark_new_or_changed = mark
    asyncio.run(r.check_word("si"))
    return r


def test_check_word_ignores_other_lemmas_in_response():
    r = _check(page(li("si"), li("sēja")), page(li("si"), li("sēja"), li("pilwi")))
    assert r.marked == [] and not r.empty


def test_check_word_detects_own_change_and_empty():
    assert _check(page(li("si", tr="a")), page(li("si", tr="b"))).marked == ["si"]
    assert _check(page(li("si")), page(li("sēsē"))).empty == {"si"}


def test_sources_only_bracketed_part():
    html = page(li("saldiskāi", desc="NOM PL N [Salds DRV, Süßigkeiten MK]"), li("x", desc="[y]"))
    assert N.sources(html, "saldiskāi") == {"[Salds DRV, Süßigkeiten MK]"}


def test_pair_renames_by_shared_source():
    vanished = {"štīri": {"[sture MK]"}, "māisteresdēlan": {"[Maisterwerk, meistardarbs Pit]"},
                "rīksnā": set()}
    cands = {"stīri": {"[sture MK]"}, "māisterjasdēlan": {"[Maisterwerk, meistardarbs Pit]"},
             "māistrjasdēlan": {"[Maisterwerk, meistardarbs Pit]"}, "paprika": {"[Paprika MK]"}}
    assert N.pair_renames(vanished, cands) == {
        "štīri": ["stīri"], "māisteresdēlan": ["māisterjasdēlan", "māistrjasdēlan"]}


def test_rename_applies_immediately():
    r = _run({"štīri", "rīksnā"}, words=["štīri", "rīksnā"], new={"stīri"})
    r.writes = {"entries/štīri/engl.html": page(li("štīri", desc="[sture MK]")).encode(),
                "entries/stīri/engl.html": page(li("stīri", desc="[sture MK]")).encode()}
    gone, pending, renamed = r.resolve_deletions({}, "2026-10-28", False)
    assert renamed == {"štīri": ["stīri"]} and gone == {"štīri"}
    assert pending == {"rīksnā": {"since": "2026-10-28"}}   # ohne Gegenstück: erst beim 2. Mal
    assert "entries/štīri/engl.html" in r.deletes and "forms/1_štīri.html" in r.deletes


class _MultiClient:
    def __init__(self, by_lang, form=b"<table/>"):
        self.by_lang, self.form = by_lang, form

    async def search(self, s, lang):
        return self.by_lang[lang]

    async def more(self, word, numb, desc):
        return self.form


class _DictStore(_Store):
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files.get(path)

    def form_path(self, w, numb):
        return f"forms/{numb}_{w}.html"

    def form_files(self, w):
        return [p for p in self.files if p.startswith("forms/") and p.endswith(f"_{w}.html")]


def _refetch_run(stored_html, live_html):
    r = N.Run.__new__(N.Run)
    files = {f"entries/si/{l}.html": stored_html.encode() for l in N.LANGUAGES}
    r.store = _DictStore(files)
    r.client = _MultiClient({l: live_html.encode() for l in N.LANGUAGES})
    r.writes, r.deletes, r.failed, r.new, r.changed = {}, set(), set(), set(), set()
    return r


def test_unconfirmed_change_is_not_counted_and_noise_not_written():
    import asyncio
    r = _refetch_run(page(li("si"), li("sēja")), page(li("si"), li("sēja"), li("pilwi")))
    asyncio.run(r.mark_new_or_changed("si", is_new=False))
    assert r.changed == set() and r.writes == {}


def test_confirmed_change_is_counted_and_written():
    import asyncio
    r = _refetch_run(page(li("si", tr="a")), page(li("si", tr="b")))
    asyncio.run(r.mark_new_or_changed("si", is_new=False))
    assert r.changed == {"si"} and len(r.writes) == len(N.LANGUAGES)
