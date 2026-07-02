#!/usr/bin/env python3
"""
Phase A3: Parse raw awizi article HTML into Markdown + structured JSON.

For each article in raw/awizi/articles/{slug}.html:
  - Extract title, date, categories, tags, content from entry-content
  - Convert content HTML to Markdown
  - Skip if text word count < 30 (image-only articles)
  - Save as parsed/awizi_articles/{slug}.md
  - Collect metadata into parsed/awizi_articles.json

Usage:
  python3 scripts/awizi_parse.py              # Run
  python3 scripts/awizi_parse.py --slug X     # Parse one article only
"""

import json
import os
import re
import sys

from bs4 import BeautifulSoup
from markdownify import markdownify as md

MIN_WORDS = 30

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "raw", "awizi", "articles")
OUT_DIR = os.path.join(ROOT, "parsed", "awizi_articles")
OUT_JSON = os.path.join(ROOT, "parsed", "awizi_articles.json")
ARTICLELIST_FILE = os.path.join(ROOT, "state", "awizi_articlelist.json")


def load_articlelist():
    with open(ARTICLELIST_FILE, encoding="utf-8") as f:
        return json.load(f)


def extract_text_words(html_fragment):
    """Return number of whitespace-separated words in HTML, tags stripped."""
    text = BeautifulSoup(html_fragment, "html.parser").get_text()
    words = text.split()
    return len(words)


def parse_article(slug, url):
    """Parse a single article HTML file. Returns dict or None if skipped."""
    path = os.path.join(RAW_DIR, f"{slug}.html")
    if not os.path.exists(path):
        return None

    html = open(path, encoding="utf-8").read()
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1", class_="entry-title")
    title = title_tag.get_text(strip=True) if title_tag else slug

    time_tag = soup.find("time", class_="entry-date")
    date_str = time_tag.get_text(strip=True) if time_tag else ""
    date_iso = time_tag.get("datetime", "")[:10] if time_tag and time_tag.get("datetime") else ""

    cats = []
    for a in soup.find_all("a", rel="category tag"):
        t = a.get_text(strip=True)
        if t and t not in cats:
            cats.append(t)

    tags = []
    for a in soup.find_all("a", rel="tag"):
        t = a.get_text(strip=True)
        if t and t not in cats and t not in tags:
            tags.append(t)

    content_div = soup.find("div", class_="entry-content")
    if not content_div:
        return None

    content_html = content_div.decode_contents().strip()
    if not content_html:
        return None

    word_count = extract_text_words(content_html)
    if word_count < MIN_WORDS:
        return {
            "slug": slug,
            "title": title,
            "url": url,
            "date": date_iso or date_str,
            "categories": cats,
            "tags": tags,
            "word_count": word_count,
            "skipped": True,
        }

    content_md = md(content_html, heading_style="ATX")

    return {
        "slug": slug,
        "title": title,
        "url": url,
        "date": date_iso or date_str,
        "categories": cats,
        "tags": tags,
        "word_count": word_count,
        "skipped": False,
        "content_md": content_md,
    }


def run(single_slug=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    articlelist = load_articlelist()
    url_by_slug = {a["slug"]: a["url"] for a in articlelist}

    slugs = list(url_by_slug.keys())

    if single_slug:
        slugs = [s for s in slugs if s == single_slug]

    results = []
    parsed_count = 0
    skipped_count = 0

    for slug in slugs:
        result = parse_article(slug, url_by_slug[slug])
        if result is None:
            continue

        if result["skipped"]:
            skipped_count += 1
            results.append({k: v for k, v in result.items() if k != "content_md"})
        else:
            parsed_count += 1
            out_path = os.path.join(OUT_DIR, f"{slug}.md")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(result["content_md"])
            results.append({k: v for k, v in result.items() if k != "content_md"})

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Parsed: {parsed_count} articles, skipped (image-only): {skipped_count}, total: {len(results)}",
          file=sys.stderr)
    print(f"Articles: {OUT_DIR}/", file=sys.stderr)
    print(f"Metadata: {OUT_JSON}", file=sys.stderr)


if __name__ == "__main__":
    if "--slug" in sys.argv:
        idx = sys.argv.index("--slug")
        run(single_slug=sys.argv[idx + 1])
    else:
        run()
