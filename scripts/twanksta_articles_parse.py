#!/usr/bin/env python3
"""
Extract article/description content (<div class="descripcio">) from raw twanksta
HTML and save as individual Markdown files.

Input:
  raw/twanksta/entries/{word}/engl.html

Output:
  parsed/twanksta_articles/{word}.md   — one file per word with a description

Usage:
  python3 scripts/twanksta_articles_parse.py
  python3 scripts/twanksta_articles_parse.py --word X   # single word
"""

import json
import os
import sys

from bs4 import BeautifulSoup
from markdownify import markdownify as md

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDLIST = os.path.join(ROOT, "state", "twanksta_wordlist.json")
ENTRIES_DIR = os.path.join(ROOT, "raw", "twanksta", "entries")
OUT_DIR = os.path.join(ROOT, "parsed", "twanksta_articles")


def extract_articles_from_html(html):
    """Return list of {word, content} dicts for all descripcio divs in the page."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for div in soup.find_all("div", class_="descripcio"):
        content = div.decode_contents().strip()
        if not content:
            continue
        li = div.find_parent("li")
        if not li:
            continue
        word_el = li.find(class_="word")
        if not word_el:
            continue
        word = word_el.get_text(strip=True)
        articles.append({"word": word, "content": content})
    return articles


def run(target_word=None):
    os.makedirs(os.path.join(ROOT, "parsed"), exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    wordlist = json.load(open(WORDLIST, encoding="utf-8"))
    word_set = {w["word"] for w in wordlist}

    if target_word:
        word_dirs = [d for d in sorted(os.listdir(ENTRIES_DIR))
                     if os.path.isdir(os.path.join(ENTRIES_DIR, d)) and d == target_word]
    else:
        word_dirs = sorted(os.listdir(ENTRIES_DIR))

    extracted = 0
    for word in word_dirs:
        entry_dir = os.path.join(ENTRIES_DIR, word)
        if not os.path.isdir(entry_dir):
            continue
        engl_path = os.path.join(entry_dir, "engl.html")
        if not os.path.exists(engl_path):
            continue

        html = open(engl_path, encoding="utf-8").read()
        articles = extract_articles_from_html(html)

        for a in articles:
            markdown = md(a["content"], heading_style="ATX")
            out_path = os.path.join(OUT_DIR, f"{a['word']}.md")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            extracted += 1

    print(f"Extracted {extracted} articles to {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    if "--word" in sys.argv:
        idx = sys.argv.index("--word")
        run(target_word=sys.argv[idx + 1])
    else:
        run()
