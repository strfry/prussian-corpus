#!/usr/bin/env python3
"""
Phase 2: Cache raw HTML for each entry in the Prussian dictionary.

For each word in state/twanksta_wordlist.json:
  - Fetches search results for all 6 languages → raw/twanksta/entries/{word}.{lang}.html
    (includes descripcio article content and all homonyms for that word)
  - If word has a paradigm number: POSTs to /more/ → raw/twanksta/forms/{num}_{word}.html
    (includes full paradigm table with participle sub-tables)

Forms are deduped by (word, paradigm) — same word+paradigm gives same form table.

Usage:
  python3 scripts/twanksta_fetch.py           # Run / resume
  python3 scripts/twanksta_fetch.py --status  # Show progress
  python3 scripts/twanksta_fetch.py --test    # Process 5 entries only
  python3 scripts/twanksta_fetch.py --word X  # Process one specific word
"""

import json
import asyncio
import time
import sys
import os
import re

import httpx

BASE = "https://wirdeins.twanksta.org"
DIALECT = "semba"
DELAY = 0.2
CONCURRENCY = 4
LANGUAGES = ["engl", "miks", "leit", "latt", "pols", "mask"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDLIST_FILE = os.path.join(ROOT, "state", "twanksta_wordlist.json")
STATE_FILE = os.path.join(ROOT, "state", "fetch_state.json")
ENTRIES_DIR = os.path.join(ROOT, "raw", "twanksta", "entries")
FORMS_DIR = os.path.join(ROOT, "raw", "twanksta", "forms")


def safe_filename(word):
    """Make a word safe for use as a filename component."""
    return re.sub(r'[/\\:*?"<>|]', "_", word)


def entry_lang_path(word):
    return os.path.join(ENTRIES_DIR, safe_filename(word))


def form_path(word, paradigm):
    return os.path.join(FORMS_DIR, f"{paradigm}_{safe_filename(word)}.html")


def load_wordlist():
    with open(WORDLIST_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"done_words": [], "done_forms": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


class AdaptiveThrottle:
    def __init__(self, min_delay=0.05, max_delay=2.0, concurrency=4, window=20):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.delay = min_delay
        self.semaphore = asyncio.Semaphore(concurrency)
        self.window = window
        self.times = []
        self.baseline = None
        self._lock = asyncio.Lock()

    async def record(self, elapsed):
        async with self._lock:
            self.times.append(elapsed)
            if len(self.times) > self.window:
                self.times.pop(0)
            if self.baseline is None:
                if len(self.times) >= self.window:
                    self.baseline = sorted(self.times)[len(self.times) // 2]
                return
            current = sorted(self.times)[len(self.times) // 2]
            ratio = current / self.baseline
            if ratio > 2.0:
                self.delay = min(self.delay * 1.5, self.max_delay)
                print(f"  [throttle] slow, delay → {self.delay:.2f}s", file=sys.stderr, flush=True)
            elif ratio < 1.3 and self.delay > self.min_delay:
                self.delay = max(self.delay * 0.8, self.min_delay)

    def stats(self):
        if not self.times:
            return "no requests"
        med = sorted(self.times)[len(self.times) // 2]
        if self.baseline:
            return f"median={med*1000:.0f}ms delay={self.delay*1000:.0f}ms"
        return f"warming up ({len(self.times)}/{self.window})"


_throttle = None
_client = None


def get_throttle():
    global _throttle
    if _throttle is None:
        _throttle = AdaptiveThrottle(min_delay=DELAY, concurrency=CONCURRENCY)
    return _throttle


def get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True, timeout=30,
            headers={"User-Agent": "PrussianCorpusScraper/1.0 (linguistic research)"},
            follow_redirects=True,
        )
    return _client


async def fetch_get(url, params=None, retries=3):
    client = get_client()
    throttle = get_throttle()
    async with throttle.semaphore:
        for attempt in range(retries):
            try:
                await asyncio.sleep(throttle.delay)
                t0 = time.monotonic()
                resp = await client.get(url, params=params)
                elapsed = time.monotonic() - t0
                await throttle.record(elapsed)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as e:
                if 500 <= e.response.status_code < 600 and attempt < retries - 1:
                    await asyncio.sleep(5 * (2 ** attempt))
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
                if attempt < retries - 1:
                    await asyncio.sleep(5 * (2 ** attempt))
                else:
                    raise


async def fetch_post(url, data, retries=3):
    client = get_client()
    throttle = get_throttle()
    async with throttle.semaphore:
        for attempt in range(retries):
            try:
                await asyncio.sleep(throttle.delay)
                t0 = time.monotonic()
                resp = await client.post(url, data=data)
                elapsed = time.monotonic() - t0
                await throttle.record(elapsed)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as e:
                if 500 <= e.response.status_code < 600 and attempt < retries - 1:
                    await asyncio.sleep(5 * (2 ** attempt))
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
                if attempt < retries - 1:
                    await asyncio.sleep(5 * (2 ** attempt))
                else:
                    raise


def write_cache(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


async def fetch_word_langs(word, done_words):
    """Fetch all 6 language search results for a word. Cache each as {word}/{lang}.html."""
    word_dir = entry_lang_path(word)
    tasks = []
    langs_to_fetch = []

    for lang in LANGUAGES:
        path = os.path.join(word_dir, f"{lang}.html")
        if not os.path.exists(path) and word not in done_words:
            langs_to_fetch.append((lang, path))

    if not langs_to_fetch:
        return

    async def fetch_one(lang, path):
        html_text = await fetch_get(BASE + "/search/", params={"s": word, "language": lang, "dia": DIALECT})
        write_cache(path, html_text)

    await asyncio.gather(*[fetch_one(lang, path) for lang, path in langs_to_fetch])


async def fetch_form(word, paradigm, desc):
    """Fetch form table for word+paradigm. Cache as forms/{num}_{word}.html."""
    path = form_path(word, paradigm)
    if os.path.exists(path):
        return
    html_text = await fetch_post(BASE + "/more/", data={
        "word": word, "numb": paradigm, "desc": desc, "dia": DIALECT,
    })
    write_cache(path, html_text)


def group_by_word(wordlist):
    """Group entries by word, collecting all paradigms/descs per word."""
    by_word = {}
    for e in wordlist:
        w = e["word"]
        if w not in by_word:
            by_word[w] = []
        by_word[w].append(e)
    return by_word


async def process_word(word, entries, done_words, done_form_keys):
    """Fetch all langs + forms for one word group (incl. homonyms)."""
    await fetch_word_langs(word, done_words)

    # Fetch forms for each unique (word, paradigm) pair
    seen_form_keys = set()
    for entry in entries:
        paradigm = entry.get("paradigm", "")
        if not paradigm:
            continue
        form_key = (word, paradigm)
        if form_key in done_form_keys or form_key in seen_form_keys:
            continue
        seen_form_keys.add(form_key)
        desc = entry.get("desc", "")
        await fetch_form(word, paradigm, desc)


async def run(test=False, single_word=None):
    wordlist = load_wordlist()
    state = load_state()
    done_words = set(state.get("done_words", []))
    done_form_keys = set(tuple(k) for k in state.get("done_forms", []))

    by_word = group_by_word(wordlist)

    if single_word:
        words_to_do = [single_word] if single_word in by_word else []
        if not words_to_do:
            print(f"Word '{single_word}' not in wordlist", file=sys.stderr)
            return
    else:
        words_to_do = [w for w in by_word if w not in done_words]

    if test:
        words_to_do = words_to_do[:5]

    total = len(by_word)
    done_count = len(done_words)
    print(f"Fetch: {done_count}/{total} words done, {len(words_to_do)} remaining", file=sys.stderr)

    for i, word in enumerate(words_to_do):
        entries = by_word[word]
        try:
            await process_word(word, entries, done_words, done_form_keys)
        except Exception as e:
            print(f"\n  [ERROR] {word}: {e}", file=sys.stderr, flush=True)
            continue

        done_words.add(word)
        for entry in entries:
            p = entry.get("paradigm", "")
            if p:
                done_form_keys.add((word, p))

        if (i + 1) % 50 == 0 or i + 1 == len(words_to_do):
            state["done_words"] = sorted(done_words)
            state["done_forms"] = [list(k) for k in done_form_keys]
            save_state(state)
            pct = (done_count + i + 1) / total * 100
            print(f"  [{done_count + i + 1}/{total} {pct:.0f}%] {word} ({get_throttle().stats()})",
                  file=sys.stderr, flush=True)

    state["done_words"] = sorted(done_words)
    state["done_forms"] = [list(k) for k in done_form_keys]
    save_state(state)
    print(f"\nDone. Entries dir: {ENTRIES_DIR}", file=sys.stderr)


def show_status():
    state = load_state()
    wordlist = load_wordlist()
    by_word = group_by_word(wordlist)
    done_words = set(state.get("done_words", []))
    done_forms = set(tuple(k) for k in state.get("done_forms", []))

    total_words = len(by_word)
    total_forms = sum(1 for e in wordlist if e.get("paradigm"))

    # Count actual cached files
    cached_langs = sum(
        1 for w in by_word
        for lang in LANGUAGES
        if os.path.exists(os.path.join(entry_lang_path(w), f"{lang}.html"))
    )
    cached_forms = sum(
        1 for e in wordlist if e.get("paradigm")
        and os.path.exists(form_path(e["word"], e["paradigm"]))
    )

    print(f"Words in wordlist:  {total_words}")
    print(f"Words done (state): {len(done_words)}")
    print(f"Lang files cached:  {cached_langs} / {total_words * len(LANGUAGES)}")
    print(f"Form files cached:  {cached_forms} / {total_forms} (entries with paradigm)")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--test" in sys.argv:
        asyncio.run(run(test=True))
    elif "--word" in sys.argv:
        idx = sys.argv.index("--word")
        asyncio.run(run(single_word=sys.argv[idx + 1]))
    else:
        asyncio.run(run())
