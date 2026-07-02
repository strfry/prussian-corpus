#!/usr/bin/env python3
"""
Phase A1: Enumerate all article URLs from awizi.twanksta.org via sitemap.

Reads post-sitemap.xml (and follow-up sitemaps if any) to extract every
article permalink. Saves the list to state/awizi_articlelist.json.

Usage:
  python3 scripts/awizi_enumerate.py           # Run
  python3 scripts/awizi_enumerate.py --status  # Show info
  python3 scripts/awizi_enumerate.py --test    # Just show what would be collected
"""

import json
import sys
import os
import re

import httpx
from bs4 import BeautifulSoup

SITEMAP_URL = "https://awizi.twanksta.org/post-sitemap.xml"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_FILE = os.path.join(ROOT, "state", "awizi_articlelist.json")


def fetch_sitemap(url):
    resp = httpx.get(url, timeout=30, follow_redirects=True,
                     headers={"User-Agent": "PrussianCorpusScraper/1.0"})
    resp.raise_for_status()
    return resp.text


def parse_sitemap(xml_text):
    """Return list of article URLs from a sitemap XML (or sitemap index)."""
    soup = BeautifulSoup(xml_text, "xml")
    urls = []

    sitemap_tags = soup.find_all("sitemap")
    if sitemap_tags:
        for sm in sitemap_tags:
            loc = sm.find("loc")
            if loc:
                sub_xml = fetch_sitemap(loc.get_text(strip=True))
                urls.extend(parse_sitemap(sub_xml))
        return urls

    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if loc:
            url = loc.get_text(strip=True)
            if url != "https://awizi.twanksta.org/":
                urls.append(url)

    return urls


def slugify(url):
    """Extract a filesystem-safe slug from an article URL."""
    path = url.replace("https://awizi.twanksta.org/", "").rstrip("/")
    safe = re.sub(r'[^a-zA-Z0-9_\-]', "_", path)
    return safe[:200]


def run(test=False):
    os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)

    xml_text = fetch_sitemap(SITEMAP_URL)
    urls = parse_sitemap(xml_text)
    urls = sorted(set(urls))

    articles = []
    for url in urls:
        articles.append({
            "url": url,
            "slug": slugify(url),
        })

    if test:
        print(f"Test mode: {len(articles)} URLs would be saved", file=sys.stderr)
        for a in articles[:5]:
            print(f"  {a['slug']} -> {a['url']}", file=sys.stderr)
        return

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(articles)} article URLs to {OUT_FILE}", file=sys.stderr)


def show_status():
    if os.path.exists(OUT_FILE):
        articles = json.load(open(OUT_FILE, encoding="utf-8"))
        print(f"Article list: {len(articles)} URLs")
    else:
        print("No article list yet. Run without --status to create it.")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--test" in sys.argv:
        run(test=True)
    else:
        run()
