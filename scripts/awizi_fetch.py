#!/usr/bin/env python3
"""
Phase A2: Fetch raw HTML for all awizi.twanksta.org articles.

Reads state/awizi_articlelist.json and caches each article's HTML
to raw/awizi/articles/{slug}.html.

Usage:
  python3 scripts/awizi_fetch.py           # Run / resume
  python3 scripts/awizi_fetch.py --status  # Show progress
  python3 scripts/awizi_fetch.py --test    # Fetch 3 articles only
  python3 scripts/awizi_fetch.py --slug X  # Fetch one specific article
"""

import json
import asyncio
import time
import sys
import os

import httpx

BASE = "https://awizi.twanksta.org"
DELAY = 0.3
CONCURRENCY = 3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLELIST_FILE = os.path.join(ROOT, "state", "awizi_articlelist.json")
STATE_FILE = os.path.join(ROOT, "state", "awizi_fetch_state.json")
ARTICLES_DIR = os.path.join(ROOT, "raw", "awizi", "articles")


def load_articlelist():
    with open(ARTICLELIST_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"done_slugs": []}


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
                print(f"  [throttle] slow, delay -> {self.delay:.2f}s", file=sys.stderr, flush=True)
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


async def fetch_get(url, retries=3):
    client = get_client()
    throttle = get_throttle()
    async with throttle.semaphore:
        for attempt in range(retries):
            try:
                await asyncio.sleep(throttle.delay)
                t0 = time.monotonic()
                resp = await client.get(url)
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


async def run(test=False, single_slug=None):
    articlelist = load_articlelist()
    state = load_state()
    done_slugs = set(state.get("done_slugs", []))

    if single_slug:
        to_do = [a for a in articlelist if a["slug"] == single_slug]
        if not to_do:
            print(f"Slug '{single_slug}' not in article list", file=sys.stderr)
            return
    else:
        to_do = [a for a in articlelist if a["slug"] not in done_slugs]

    if test:
        to_do = to_do[:3]

    total = len(articlelist)
    done_count = len(done_slugs)
    print(f"Fetch: {done_count}/{total} done, {len(to_do)} remaining", file=sys.stderr)

    for i, article in enumerate(to_do):
        slug = article["slug"]
        url = article["url"]
        out_path = os.path.join(ARTICLES_DIR, f"{slug}.html")

        if os.path.exists(out_path) and slug in done_slugs:
            continue

        try:
            html_text = await fetch_get(url)
            write_cache(out_path, html_text)
        except Exception as e:
            print(f"\n  [ERROR] {slug}: {e}", file=sys.stderr, flush=True)
            continue

        done_slugs.add(slug)

        if (i + 1) % 20 == 0 or i + 1 == len(to_do):
            state["done_slugs"] = sorted(done_slugs)
            save_state(state)
            pct = (done_count + i + 1) / total * 100
            print(f"  [{done_count + i + 1}/{total} {pct:.0f}%] {slug} ({get_throttle().stats()})",
                  file=sys.stderr, flush=True)

    state["done_slugs"] = sorted(done_slugs)
    save_state(state)
    print(f"\nDone. Articles dir: {ARTICLES_DIR}", file=sys.stderr)


def show_status():
    articlelist = load_articlelist()
    state = load_state()
    done_slugs = set(state.get("done_slugs", []))

    cached = sum(1 for a in articlelist
                 if os.path.exists(os.path.join(ARTICLES_DIR, f"{a['slug']}.html")))

    print(f"Articles in list: {len(articlelist)}")
    print(f"Done (state):     {len(done_slugs)}")
    print(f"Files cached:     {cached}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--test" in sys.argv:
        asyncio.run(run(test=True))
    elif "--slug" in sys.argv:
        idx = sys.argv.index("--slug")
        asyncio.run(run(single_slug=sys.argv[idx + 1]))
    else:
        asyncio.run(run())
