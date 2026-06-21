#!/usr/bin/env python3
"""
Phase 1: Enumerate all words in the Prussian dictionary at wirdeins.twanksta.org
via recursive prefix search.

Output: state/twanksta_wordlist.json

Usage:
  python3 scripts/twanksta_enumerate.py           # Run / resume
  python3 scripts/twanksta_enumerate.py --status  # Show progress
  python3 scripts/twanksta_enumerate.py --test    # Dry run with 3 prefixes
"""

import json
import asyncio
import time
import sys
import os

import httpx
from bs4 import BeautifulSoup

BASE = "https://wirdeins.twanksta.org"
DIALECT = "semba"
DELAY = 0.2
RESULT_CAP = 30
CONCURRENCY = 4

ALPHABET = sorted(set(list("abdeghijklmnoprstuwz") + ["ā", "ē", "ī", "ō", "ū", "š", "ž"]))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDLIST_FILE = os.path.join(ROOT, "state", "twanksta_wordlist.json")
STATE_FILE = os.path.join(ROOT, "state", "enumerate_state.json")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"done_prefixes": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_wordlist():
    if os.path.exists(WORDLIST_FILE):
        with open(WORDLIST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_wordlist(words):
    with open(WORDLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)


def entry_key(e):
    return (e["word"], e["paradigm"], e["desc"])


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
            return "no requests yet"
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


async def fetch(url, params=None, retries=3):
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


def parse_search_results(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    results = []
    for li in soup.find_all("li"):
        word_el = li.find(class_="word")
        if not word_el:
            continue
        word = word_el.get_text(strip=True)
        if not word:
            continue

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

        results.append({
            "word": word,
            "paradigm": numb_el.get_text(strip=True) if numb_el else "",
            "gender": gend_el.get_text(strip=True) if gend_el else "",
            "desc": desc_el.get_text(strip=True) if desc_el else "",
            "audio": BASE + audio_el["src"] if audio_el and audio_el.get("src") else "",
            "translations_engl": translations,
        })
    return results


async def search_prefix_recursive(prefix, existing, max_depth=6):
    params = {"s": prefix, "language": "engl", "dia": DIALECT}
    text = await fetch(BASE + "/search/", params=params)
    results = parse_search_results(text)
    new_entries = []

    for r in results:
        k = entry_key(r)
        if k not in existing:
            existing.add(k)
            new_entries.append(r)

    if len(results) >= RESULT_CAP and len(prefix) < max_depth:
        tasks = [search_prefix_recursive(prefix + letter, existing, max_depth)
                 for letter in ALPHABET]
        for sub_entries in await asyncio.gather(*tasks):
            new_entries.extend(sub_entries)

    return new_entries


async def run(test=False):
    state = load_state()
    wordlist = load_wordlist()
    existing = {entry_key(e) for e in wordlist}
    done_prefixes = set(state.get("done_prefixes", []))

    all_2letter = [a + b for a in ALPHABET for b in ALPHABET]
    remaining = [p for p in all_2letter if p not in done_prefixes]

    if test:
        remaining = remaining[:3]
        print(f"Test mode: processing {remaining}", file=sys.stderr)

    print(f"Enumerate: {len(done_prefixes)}/{len(all_2letter)} prefixes done, {len(wordlist)} words", file=sys.stderr)

    batch_size = 27
    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start:batch_start + batch_size]
        tasks = [search_prefix_recursive(p, existing) for p in batch]
        results = await asyncio.gather(*tasks)

        for prefix, new_entries in zip(batch, results):
            wordlist.extend(new_entries)
            done_prefixes.add(prefix)

        state["done_prefixes"] = sorted(done_prefixes)
        save_wordlist(wordlist)
        save_state(state)
        letter = batch[0][0]
        print(f"  '{letter}*': {len(wordlist)} words ({get_throttle().stats()})", file=sys.stderr, flush=True)

    print(f"\nDone: {len(wordlist)} entries in {WORDLIST_FILE}", file=sys.stderr)


def show_status():
    state = load_state()
    wordlist = load_wordlist()
    done = set(state.get("done_prefixes", []))
    all_prefixes = [a + b for a in ALPHABET for b in ALPHABET]
    print(f"Prefixes: {len(done)}/{len(all_prefixes)} done")
    print(f"Wordlist: {len(wordlist)} entries")
    with_paradigm = sum(1 for e in wordlist if e.get("paradigm"))
    print(f"With paradigm: {with_paradigm}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--test" in sys.argv:
        asyncio.run(run(test=True))
    else:
        asyncio.run(run())
