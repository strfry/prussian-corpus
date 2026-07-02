#!/usr/bin/env python3
"""
Deduplicate YouTube subtitle segments into a sentence-level corpus.

Input:
  parsed/youtube_corpus.json    — per-video subtitle tracks

Output:
  parsed/youtube_corpus_sentences.json

Schema:
  text_clean    — Prussian text with [=…] glosses and PR:/LT:/EN: prefixes removed
  text          — original verbatim Prussian text (preserved)
  text_norm     — fully normalized for deduplication (lowercase, no glosses, NFC)
  frequency     — how many segments match this unique Prussian text
  translations  — [{text, count}] unique translations sorted by frequency
  sources       — [{video_id, title, sub_lang, start, end, translation|null}]

Usage:
  python3 scripts/youtube_dedup.py
"""

import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_FILE = os.path.join(ROOT, "parsed", "youtube_corpus.json")
OUT_FILE = os.path.join(ROOT, "parsed", "youtube_corpus_sentences.json")

GLOSS_RE = re.compile(r"\s*\[=[^]]*\]")
PREFIX_RE = re.compile(r"^(PR|LT|EN):\s+")


def normalize(text):
    """Normalize Prussian text for dedup comparison."""
    t = GLOSS_RE.sub("", text)
    t = PREFIX_RE.sub("", t)
    t = t.lower()
    t = unicodedata.normalize("NFC", t)
    t = " ".join(t.split())
    return t


def clean_text(text):
    """Remove glosses and prefixes but keep original case."""
    t = GLOSS_RE.sub("", text)
    t = PREFIX_RE.sub("", t)
    t = " ".join(t.split())
    return t


def main():
    print("Loading corpus...")
    with open(SRC_FILE, encoding="utf-8") as fh:
        tracks = json.load(fh)
    print(f"  {len(tracks)} subtitle tracks")

    grouped = {}
    total_segments = 0
    skipped_empty = 0

    for track in tracks:
        for seg in track["segments"]:
            total_segments += 1
            raw = seg.get("text", "").strip()
            if not raw:
                skipped_empty += 1
                continue

            key = normalize(raw)

            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "text_clean": clean_text(raw),
                    "text": raw,
                    "text_norm": key,
                    "frequency": 0,
                    "sources": [],
                }
                grouped[key] = entry

            entry["frequency"] += 1
            entry["sources"].append({
                "video_id": track["video_id"],
                "title": track["title"],
                "sub_lang": track["sub_lang"],
                "start": seg["start"],
                "end": seg["end"],
                "translation": seg.get("translation"),
            })

    # Collect unique translations per entry
    for entry in grouped.values():
        trans_map = {}
        for src in entry["sources"]:
            t = src.get("translation")
            if t and t.strip():
                trans_map[t.strip()] = trans_map.get(t.strip(), 0) + 1
        entry["translations"] = sorted(
            [{"text": t, "count": c} for t, c in trans_map.items()],
            key=lambda x: -x["count"],
        )

    sentences = sorted(
        grouped.values(),
        key=lambda e: (-e["frequency"], e["text_clean"].lower()),
    )

    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(sentences, fh, ensure_ascii=False, indent=2)

    unique_pairs = sum(1 for s in sentences if any(
        src["translation"] for src in s["sources"]
    ))
    print(f"  {total_segments} segments → {len(sentences)} unique ({skipped_empty} empty skipped)")
    print(f"  {unique_pairs} unique segments have at least one translation")
    print(f"Output: {OUT_FILE}")


if __name__ == "__main__":
    main()
