#!/usr/bin/env python3
"""
Deduplicate YouTube subtitle sentences into a corpus.

First merges adjacent SRT segments into full sentences, then deduplicates
the resulting sentences across all videos.

Input:
  parsed/youtube_corpus.json    — per-video subtitle tracks (raw segments)

Output:
  parsed/youtube_corpus_sentences.json

Schema:
  text_clean    — Prussian text with [=…] glosses and PR:/LT:/EN: prefixes removed
  text          — original verbatim Prussian text (preserved)
  text_norm     — fully normalized for deduplication (lowercase, no glosses, NFC)
  frequency     — how many sentences match this unique Prussian text
  translations  — [{text, count}] unique translations sorted by frequency
  sources       — [{video_id, title, sub_lang, start, end, translation|null, segment_count}]

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
PREFIX_RE = re.compile(r"^(PR|LT|EN|ENG):\s*")
SENTENCE_END_RE = re.compile(r'[.!?…]$|\.{2,}$')
TRAILING_PUNCT_RE = re.compile(r'[,\s]+$')


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


def strip_prefix(text):
    """Strip language prefix from the beginning of text."""
    return PREFIX_RE.sub("", text)


def clean_translation(tr):
    """Clean translation text: strip prefixes, quotes, parens."""
    if not tr:
        return tr
    t = strip_prefix(tr).strip()
    quotes = {'"', '\u201c', '\u201d', '\u201e'}

    while t and t[0] in quotes:
        t = t[1:].lstrip()
    while t and t[-1] in quotes:
        t = t[:-1].rstrip()
    if len(t) >= 2 and t[0] == '(' and t[-1] == ')':
        t = t[1:-1].strip()

    return t


def _strip_annotations(text):
    """Strip glosses and prefixes for analysis, not storage."""
    t = GLOSS_RE.sub("", text)
    t = PREFIX_RE.sub("", t)
    return t


def is_sentence_end(text):
    """Check if text ends a sentence, ignoring trailing commas/spaces."""
    t = _strip_annotations(text)
    t = TRAILING_PUNCT_RE.sub("", t)
    if not t:
        return False
    return bool(SENTENCE_END_RE.search(t))


def starts_lower(text):
    """Check if clean text starts with a lowercase letter."""
    t = _strip_annotations(text).strip()
    return t and t[0].islower()


def ends_with(text, chars):
    """Check if clean text ends with any of the given characters."""
    t = _strip_annotations(text).strip()
    return t and t[-1] in chars


def should_merge(current_text, next_text):
    if is_sentence_end(current_text):
        return False

    if ends_with(current_text, ',;:'):
        return True

    if starts_lower(next_text):
        return True

    return False


def detect_format(track):
    segs = track["segments"]
    sample = segs[:min(10, len(segs))]

    has_pr_text = any(s["text"].startswith("PR:") for s in sample)
    has_pr_in_tr = any(
        s.get("translation", "").startswith("PR:") for s in sample
        if s.get("translation")
    )

    if has_pr_text and has_pr_in_tr:
        return "multi_lang"
    elif has_pr_text:
        return "prefix_bilingual"
    else:
        return "simple"


def extract_clean_texts(track):
    format_type = detect_format(track)
    results = []

    for seg in track["segments"]:
        raw_text = seg["text"]
        raw_tr = seg.get("translation", "")

        if format_type == "multi_lang":
            if not raw_text.startswith("PR:"):
                continue
            prussian = strip_prefix(raw_text)
            translation = clean_translation(raw_tr) if raw_tr else None
        elif format_type == "prefix_bilingual":
            prussian = strip_prefix(raw_text)
            translation = clean_translation(raw_tr) if raw_tr else None
        else:
            prussian = raw_text
            translation = clean_translation(raw_tr) if raw_tr else None

        prussian = prussian.strip()
        if not prussian:
            continue

        results.append({
            "text": prussian,
            "translation": translation,
            "start": seg["start"],
            "end": seg["end"],
            "index": seg["index"],
        })

    return results


def merge_into_sentences(clean_segs):
    if not clean_segs:
        return []

    sentences = []
    current = None

    for seg in clean_segs:
        if current is None:
            current = {
                "text": seg["text"],
                "start": seg["start"],
                "end": seg["end"],
                "translation": seg.get("translation"),
                "segment_count": 1,
            }
            continue

        if should_merge(current["text"], seg["text"]):
            current["text"] += " " + seg["text"]
            current["end"] = seg["end"]
            if seg.get("translation"):
                if current.get("translation"):
                    current["translation"] += " " + seg["translation"]
                else:
                    current["translation"] = seg["translation"]
            current["segment_count"] += 1
        else:
            sentences.append(current)
            current = {
                "text": seg["text"],
                "start": seg["start"],
                "end": seg["end"],
                "translation": seg.get("translation"),
                "segment_count": 1,
            }

    if current:
        sentences.append(current)

    return sentences


def main():
    print("Loading corpus...")
    with open(SRC_FILE, encoding="utf-8") as fh:
        tracks = json.load(fh)
    print(f"  {len(tracks)} subtitle tracks")

    all_sentences = []
    total_segments = 0
    merged_count = 0

    for track in tracks:
        clean_segs = extract_clean_texts(track)
        total_segments += len(track["segments"])

        if not clean_segs:
            continue

        sentences = merge_into_sentences(clean_segs)

        for sent in sentences:
            text = sent["text"].strip()
            if not text:
                continue
            all_sentences.append({
                "video_id": track["video_id"],
                "title": track["title"],
                "sub_lang": track["sub_lang"],
                "start": sent["start"],
                "end": sent["end"],
                "text": text,
                "translation": sent.get("translation"),
                "segment_count": sent["segment_count"],
            })
            merged_count += 1

    print(f"  {total_segments} segments → {merged_count} sentences")

    grouped = {}
    skipped_empty = 0

    for sent in all_sentences:
        raw = sent["text"].strip()
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
            "video_id": sent["video_id"],
            "title": sent["title"],
            "sub_lang": sent["sub_lang"],
            "start": sent["start"],
            "end": sent["end"],
            "translation": sent.get("translation"),
            "segment_count": sent["segment_count"],
        })

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

    sentences_out = sorted(
        grouped.values(),
        key=lambda e: (-e["frequency"], e["text_clean"].lower()),
    )

    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(sentences_out, fh, ensure_ascii=False, indent=2)

    unique_pairs = sum(1 for s in sentences_out if s["translations"])
    print(f"  {merged_count} sentences → {len(sentences_out)} unique ({skipped_empty} empty skipped)")
    print(f"  {unique_pairs} unique sentences have at least one translation")
    print(f"Output: {OUT_FILE}")


if __name__ == "__main__":
    main()
