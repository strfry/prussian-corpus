#!/usr/bin/env python3
"""
Nightly incremental refresh of raw/twanksta.

Each night sweeps the whole vocabulary in one language, rotating
engl → miks → leit → latt → pols → mask: recursive prefix search over
/search/?s=<prefix>% (the prefix tier caps at 10 lemmas, so a prefix is split
when it returns ≥ 10 prefix hits). Each lemma's own <li> blocks are compared with
the stored file of that language, and new lemmas are discovered in the same pass.
Most edits are on the Prussian side (spelling, desc, paradigm), which every
language shows, so every night catches them and finds new lemmas; the rotation
only matters for edits to the translations themselves. Lemmas only seen in capped
responses get a single /search/?s=<lemma> query.

A lemma that is new or changed in any language gets all 6 languages and its form
tables (/more/) refetched. Form tables are never refreshed on their own: they are
generated from stems and cannot be edited on the site.

Site behaviour this relies on (measured 2026-10-28):
  - responses are byte-deterministic;
  - a lemma's <li> in a <prefix>% response is identical to the one in its own
    response (descripcio articles included);
  - % and _ are passed through as SQL LIKE, matching is case- and diacritic-insensitive;
  - single-lemma queries return (almost) only the lemma itself, so new lemmas can
    only be found by the sweep (which also lets renames pair up the same night).

Nothing is written until the run has passed its sanity checks; on failure the
script exits non-zero and leaves raw/twanksta untouched. Summary goes to stdout,
progress to stderr.

Usage:
  python3 scripts/twanksta_nightly.py                 # run the due language
  python3 scripts/twanksta_nightly.py --lang miks     # force a language (does not advance rotation)
  python3 scripts/twanksta_nightly.py --sweep-root ab # sweep below one prefix only (testing)
  python3 scripts/twanksta_nightly.py --dry-run       # check, but write nothing
  python3 scripts/twanksta_nightly.py --status        # show rotation state
"""

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time
import unicodedata

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from twanksta_fetch import AdaptiveThrottle, LANGUAGES, safe_filename  # noqa: E402

BASE = "https://wirdeins.twanksta.org"
DIALECT = "semba"
USER_AGENT = "PrussianCorpusScraper/2.0 (+https://prussian.uber.space)"
DELAY = 0.2
CONCURRENCY = 3
PREFIX_CAP = 10          # /search/ returns at most 10 prefix-tier lemmas
MAX_PREFIX_LEN = 24
STALE_DAYS = 8
MAX_EMPTY_RATIO = 0.02   # more empty answers for known lemmas than this ⇒ abort
MAX_ERROR_RATIO = 0.01

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RAW = os.path.join(ROOT, "raw", "twanksta")
CORPUS_WORDLIST = os.path.join(ROOT, "state", "twanksta_wordlist.json")

NOTHING_FOUND = "search-status"


# ---------------------------------------------------------------- pure helpers

def norm(s):
    """Approximate the site's matching: case- and diacritic-insensitive."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if not unicodedata.combining(c))


def li_blocks(html):
    """[(word, str(li)), ...] for every result <li> in a /search/ response."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.find_all("li"):
        w = li.find(class_="word")
        if w:
            out.append((w.get_text(strip=True), str(li)))
    return out


def li_signature(blocks, word):
    """Order-independent signature of all <li> blocks of one lemma (homonyms included)."""
    return tuple(sorted(li for w, li in blocks if w == word))


SOURCE_RE = re.compile(r"\[[^\]]*\]")


def sources(html, word):
    """Bracketed source references ("[sture MK]") in the desc of the lemma's entries."""
    soup = BeautifulSoup(html, "html.parser")
    out = set()
    for li in soup.find_all("li"):
        w = li.find(class_="word")
        desc = li.find(class_="desc")
        if w and desc and w.get_text(strip=True) == word:
            out.update(SOURCE_RE.findall(desc.get_text(" ", strip=True)))
    return out


def pair_renames(vanished, candidates):
    """{old: [new, ...]} for lemmas sharing a bracketed source reference.

    vanished / candidates: {lemma: set(sources)}."""
    pairs = {}
    for old, src in vanished.items():
        to = sorted(n for n, nsrc in candidates.items() if src & nsrc)
        if to:
            pairs[old] = to
    return pairs


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def typo_fix(old, candidates):
    """The one candidate within a typo's reach of old (normalized), else None."""
    n = norm(old)
    limit = 2 if len(n) >= 8 else 1
    close = [c for c in candidates if edit_distance(n, norm(c)) <= limit]
    return close[0] if len(close) == 1 else None


def form_keys(html, word):
    """{(numb, desc)} of the lemma's entries that have a paradigm table."""
    soup = BeautifulSoup(html, "html.parser")
    keys = set()
    for li in soup.find_all("li"):
        w = li.find(class_="word")
        numb = li.find(class_="numb")
        if not w or w.get_text(strip=True) != word or not numb:
            continue
        desc = li.find(class_="desc")
        keys.add((numb.get_text(strip=True), desc.get_text(strip=True) if desc else ""))
    return keys


def is_complete(blocks, prefix):
    """True if a <prefix>% response lists every lemma starting with prefix."""
    return sum(1 for w, _ in blocks if norm(w).startswith(prefix)) < PREFIX_CAP


def follow_chars(words):
    """{char: set(next chars)} over normalized lemmas, plus the start alphabet under ''."""
    follow = {"": set()}
    for w in words:
        n = norm(w)
        if not n:
            continue
        follow[""].add(n[0])
        for a, b in zip(n, n[1:]):
            follow.setdefault(a, set()).add(b)
    for k in follow:
        follow[k] -= {"%", "_"}     # LIKE wildcards: lemmas containing them come via fallback
        if k:
            follow[k].add(" ")
    return follow


# ------------------------------------------------------------------- storage

class Store:
    def __init__(self, raw):
        self.raw = raw
        self.entries = os.path.join(raw, "entries")
        self.forms = os.path.join(raw, "forms")
        self.state_dir = os.path.join(raw, "state")
        self.wordlist_path = os.path.join(self.state_dir, "wordlist.json")
        self.rotation_path = os.path.join(self.state_dir, "rotation.json")
        self.pending_path = os.path.join(self.state_dir, "pending_deletions.json")

    def _load(self, path, default):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return default

    def wordlist(self):
        words = self._load(self.wordlist_path, None)
        if words is None:   # bootstrap from the corpus wordlist of the old scraper
            words = [e["word"] for e in self._load(CORPUS_WORDLIST, [])]
        return sorted(set(words))

    def rotation(self):
        return self._load(self.rotation_path, {"next": LANGUAGES[0], "last_verified": {}})

    def pending(self):
        raw = self._load(self.pending_path, {})
        return {w: v if isinstance(v, dict) else {"since": v} for w, v in raw.items()}

    def entry_path(self, word, lang):
        return os.path.join(self.entries, safe_filename(word), f"{lang}.html")

    def form_path(self, word, numb):
        return os.path.join(self.forms, f"{numb}_{safe_filename(word)}.html")

    def read(self, path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def form_files(self, word):
        suffix = f"_{safe_filename(word)}.html"
        if not os.path.isdir(self.forms):
            return []
        return [os.path.join(self.forms, f) for f in os.listdir(self.forms)
                if f.endswith(suffix) and f[:-len(suffix)].isdigit()]


# ------------------------------------------------------------------- network

class Client:
    def __init__(self):
        self.http = httpx.AsyncClient(http2=True, timeout=30, follow_redirects=True,
                                      headers={"User-Agent": USER_AGENT})
        self.throttle = AdaptiveThrottle(min_delay=DELAY, concurrency=CONCURRENCY)
        self.requests = 0
        self.errors = 0

    async def _do(self, method, url, retries=3, **kw):
        async with self.throttle.semaphore:
            for attempt in range(retries):
                try:
                    await asyncio.sleep(self.throttle.delay)
                    t0 = time.monotonic()
                    resp = await self.http.request(method, url, **kw)
                    await self.throttle.record(time.monotonic() - t0)
                    self.requests += 1
                    resp.raise_for_status()
                    return resp.content
                except (httpx.HTTPStatusError, httpx.TransportError) as e:
                    status = getattr(getattr(e, "response", None), "status_code", 500)
                    if status < 500 or attempt == retries - 1:
                        self.errors += 1
                        print(f"  [error] {method} {kw.get('params') or kw.get('data')}: {e}",
                              file=sys.stderr, flush=True)
                        return None
                    await asyncio.sleep(5 * 2 ** attempt)

    async def search(self, s, lang):
        return await self._do("GET", BASE + "/search/",
                              params={"s": s, "language": lang, "dia": DIALECT})

    async def more(self, word, numb, desc):
        return await self._do("POST", BASE + "/more/",
                              data={"word": word, "numb": numb, "desc": desc, "dia": DIALECT})

    async def close(self):
        await self.http.aclose()


def text(content):
    return content.decode("utf-8-sig", errors="replace")


# ------------------------------------------------------------------- the run

class Run:
    def __init__(self, store, client, lang):
        self.store = store
        self.client = client
        self.lang = lang
        self.words = store.wordlist()
        self.writes = {}        # path -> bytes
        self.deletes = set()    # paths
        self.new, self.changed, self.empty = set(), set(), set()
        self.redirects = {}     # vanished lemma -> lemmas the site returns for it
        self.failed = set()     # lemmas whose check could not be completed

    # -- refetch everything for one lemma

    async def refetch_lemma(self, word):
        """Refetch all languages + form tables; return True if anything of the lemma changed.

        A language file is only rewritten when the lemma's own <li> blocks differ,
        so other lemmas listed in the same response never produce a diff."""
        results = await asyncio.gather(*[self.client.search(word, l) for l in LANGUAGES])
        if any(r is None for r in results):
            self.failed.add(word)
            return False
        touched = False
        engl = None
        for lang, content in zip(LANGUAGES, results):
            path = self.store.entry_path(word, lang)
            stored = self.store.read(path)
            if stored is None or li_signature(li_blocks(text(stored)), word) != \
                    li_signature(li_blocks(text(content)), word):
                self.writes[path] = content
                touched = True
            if lang == "engl":
                engl = content
        keys = form_keys(text(engl), word)
        keep = set()
        for numb, desc in sorted(keys):
            path = self.store.form_path(word, numb)
            if path in keep:
                continue        # homonyms with the same paradigm share one table
            keep.add(path)
            content = await self.client.more(word, numb, desc)
            if content is None:
                self.failed.add(word)
                continue
            if self.store.read(path) != content:
                self.writes[path] = content
                touched = True
        for path in self.store.form_files(word):
            if path not in keep:
                self.deletes.add(path)
                touched = True
        return touched

    async def mark_new_or_changed(self, word, is_new):
        if is_new:
            self.new.add(word)
            await self.refetch_lemma(word)
        elif await self.refetch_lemma(word):
            # only count changes the refetch confirms (guards against odd one-off answers)
            self.changed.add(word)

    # -- single-lemma check (sweep fallback)

    async def check_word(self, word):
        content = await self.client.search(word, self.lang)
        if content is None:
            self.failed.add(word)
            return
        blocks = li_blocks(text(content))
        sig = li_signature(blocks, word)
        if not sig:
            self.empty.add(word)
            # the site still finds old spellings as variants of the new lemma
            self.redirects[word] = sorted({w for w, _ in blocks})
            return
        # Compare only the lemma's own <li> blocks: the rest of the response lists
        # other lemmas that merely match the query and changes whenever they do.
        stored = self.store.read(self.store.entry_path(word, self.lang))
        if stored is None or li_signature(li_blocks(text(stored)), word) != sig:
            await self.mark_new_or_changed(word, is_new=False)

    # -- sweep

    async def sweep(self, prefix, follow, seen, complete_sigs):
        content = await self.client.search(prefix + "%", self.lang)
        if content is None:
            self.sweep_failed.add(prefix)
            return
        blocks = li_blocks(text(content))
        for w, _ in blocks:
            seen.add(w)
        if is_complete(blocks, prefix) or len(prefix) >= MAX_PREFIX_LEN:
            if not is_complete(blocks, prefix):
                print(f"  [warn] prefix {prefix!r} still capped at max length", file=sys.stderr)
                return
            for w in {w for w, _ in blocks if norm(w).startswith(prefix)}:
                complete_sigs[w] = li_signature(blocks, w)
            return
        kids = sorted(follow.get(prefix[-1] if prefix else "", set()))
        await asyncio.gather(*[self.sweep(prefix + c, follow, seen, complete_sigs) for c in kids])

    async def run_sweep(self, root=None):
        follow = follow_chars(self.words)
        seen, sigs = set(), {}
        self.sweep_failed = set()
        roots = [root] if root else sorted(follow[""])
        for r in roots:
            await self.sweep(r, follow, seen, sigs)
            print(f"  [sweep] {r}: {len(sigs)} lemmas verified, {self.client.requests} requests "
                  f"({self.client.throttle.stats()})", file=sys.stderr, flush=True)
        if self.sweep_failed:
            self.failed |= {f"<prefix {p}>" for p in self.sweep_failed}

        known = set(self.words)
        scope = [w for w in self.words if not root or norm(w).startswith(root)]
        self.checked = len(scope)
        tasks = []
        for w in sorted(seen - known):
            tasks.append(self.mark_new_or_changed(w, is_new=True))
        fallback = []
        for w in scope:
            if w in sigs:
                stored = self.store.read(self.store.entry_path(w, self.lang))
                if stored is None or li_signature(li_blocks(text(stored)), w) != sigs[w]:
                    tasks.append(self.mark_new_or_changed(w, is_new=False))
            else:
                fallback.append(w)   # only seen in capped responses, or not at all
        self.fallback = len(fallback)
        tasks += [self.check_word(w) for w in fallback]
        for i in range(0, len(tasks), 200):
            await asyncio.gather(*tasks[i:i + 200])

    # -- apply

    def current(self, path):
        """File content as it will be after this run."""
        return self.writes[path] if path in self.writes else self.store.read(path)

    def resolve_deletions(self, pending, today, partial):
        """Decide what disappears. Returns (gone, new_pending, renamed).

        A vanished lemma is a rename (or a merge into an existing lemma) and goes
        at once, so git sees the files move, if it shares a bracketed source
        reference with a lemma found new in the same run, if the site answers
        the old spelling with exactly one other lemma, or if exactly one new lemma
        is a typo's distance away. Any other lemma is deleted only after coming back empty twice
        in a row. Partial (test) runs never delete and leave the pending list alone."""
        if partial:
            return set(), dict(pending), {}
        fresh = {w for w in self.empty if w not in pending}
        vanished = {w: sources(text(self.current(self.store.entry_path(w, "engl")) or b""), w)
                    for w in fresh}
        candidates = {n: sources(text(self.current(self.store.entry_path(n, "engl")) or b""), n)
                      for n in self.new}
        renamed = pair_renames(vanished, candidates)
        for w in fresh | (self.empty & set(pending)):
            hits = self.redirects.get(w, [])
            if w not in renamed and len(hits) == 1 and hits[0] != w:
                renamed[w] = hits      # sole answer to the old spelling: renamed/merged
        for w in fresh - set(renamed):
            fix = typo_fix(w, self.new)
            if fix:
                renamed[w] = [fix]     # corrected typo (elekrāina → elektrāina)
        gone = {w for w in self.empty if w in pending} | set(renamed)
        new_pending = {w: {"since": today} for w in fresh - set(renamed)}
        # Lemmas can share a directory (safe_filename maps "ēit?" and "ēit_" alike);
        # never delete files a surviving lemma still uses.
        live = {safe_filename(w) for w in (set(self.words) | self.new) - gone}
        for w in gone:
            if safe_filename(w) in live:
                continue
            for lang in LANGUAGES:
                self.deletes.add(self.store.entry_path(w, lang))
            self.deletes.update(self.store.form_files(w))
        return gone, new_pending, renamed


def write_atomic(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)


def dump_json(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def sync_corpus_wordlist(words):
    """Keep state/twanksta_wordlist.json (read by twanksta_parse.py) in step."""
    old = []
    if os.path.exists(CORPUS_WORDLIST):
        with open(CORPUS_WORDLIST, encoding="utf-8") as f:
            old = json.load(f)
    keep = set(words)
    merged = [e for e in old if e["word"] in keep]
    known = {e["word"] for e in merged}
    merged += [{"word": w, "paradigm": ""} for w in words if w not in known]
    if merged != old:
        os.makedirs(os.path.dirname(CORPUS_WORDLIST), exist_ok=True)
        with open(CORPUS_WORDLIST, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)


async def main_async(args):
    store = Store(args.raw)
    rotation = store.rotation()
    lang = args.lang or rotation["next"]
    today = datetime.date.today().isoformat()
    t0 = time.monotonic()

    client = Client()
    run = Run(store, client, lang)
    try:
        await run.run_sweep(args.sweep_root)
    finally:
        await client.close()

    partial = bool(args.sweep_root)
    pending = store.pending()
    gone, new_pending, renamed = run.resolve_deletions(pending, today, partial)

    checked = max(run.checked, 1)
    problems = []
    if len(run.empty) / checked > MAX_EMPTY_RATIO:
        problems.append(f"{len(run.empty)} known lemmas came back empty (> {MAX_EMPTY_RATIO:.0%})")
    if client.errors / max(client.requests + client.errors, 1) > MAX_ERROR_RATIO:
        problems.append(f"{client.errors} failed requests")
    if run.failed:
        problems.append(f"{len(run.failed)} lemmas/prefixes could not be checked: "
                        + ", ".join(sorted(run.failed)[:20]))

    words = sorted((set(run.words) | run.new) - gone)
    minutes = (time.monotonic() - t0) / 60

    # Language-neutral block first: the wrapper uses everything up to the first
    # blank line as the commit message. Details for the mail follow.
    targets = {t for to in renamed.values() for t in to}
    added = run.new - targets
    print(f"twanksta {lang} {today}  +{len(added)} ~{len(run.changed)} "
          f"-{len(gone) - len(renamed)} >{len(renamed)}")
    for sign, items in (("+", added), ("~", run.changed), ("-", gone - set(renamed))):
        if items:
            print(f"{sign} " + ", ".join(sorted(items)))
    if renamed:
        print("> " + ", ".join(f"{o} → {' | '.join(t)}" for o, t in sorted(renamed.items())))
    print()
    print(f"{run.checked} lemmas checked, {client.requests} requests, {minutes:.0f} min")
    print(f"  sweep fallback single queries: {run.fallback}")
    fresh = sorted(set(new_pending) - set(pending))
    if fresh:
        print("  empty once (pending deletion): " + ", ".join(fresh))
    print(f"  files: {len(run.writes)} written, {len(run.deletes)} deleted")

    last = dict(rotation.get("last_verified", {}))
    if not partial:
        last[lang] = today
    for l in LANGUAGES:
        d = last.get(l)
        if not d or (datetime.date.today() - datetime.date.fromisoformat(d)).days > STALE_DAYS:
            print(f"  WARNING: {l} last fully verified {d or 'never'}")

    if problems:
        print("ABORTED, nothing written:")
        for p in problems:
            print(f"  - {p}")
        return 1
    if args.dry_run:
        print("dry run, nothing written")
        return 0

    for path, content in run.writes.items():
        write_atomic(path, content)
    for path in run.deletes:
        if os.path.exists(path):
            os.remove(path)
            d = os.path.dirname(path)
            if d.startswith(store.entries) and not os.listdir(d):
                os.rmdir(d)
    write_atomic(store.wordlist_path, dump_json(words))
    write_atomic(store.pending_path, dump_json(new_pending))
    if not args.lang and not partial:
        rotation["next"] = LANGUAGES[(LANGUAGES.index(lang) + 1) % len(LANGUAGES)]
    rotation["last_verified"] = last
    write_atomic(store.rotation_path, dump_json(rotation))
    if os.path.realpath(args.raw) == os.path.realpath(DEFAULT_RAW):
        sync_corpus_wordlist(words)
    return 0


def show_status(raw):
    store = Store(raw)
    rot = store.rotation()
    print(f"raw dir:   {raw}")
    print(f"lemmas:    {len(store.wordlist())}")
    print(f"next lang: {rot['next']}")
    for l in LANGUAGES:
        print(f"  {l}: last verified {rot.get('last_verified', {}).get(l, 'never')}")
    print(f"pending deletions: {len(store.pending())}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--raw", default=DEFAULT_RAW)
    ap.add_argument("--lang", choices=LANGUAGES)
    ap.add_argument("--sweep-root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        show_status(args.raw)
        return 0
    if args.sweep_root:
        args.sweep_root = norm(args.sweep_root)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
