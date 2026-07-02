#!/usr/bin/env python3
"""
Parse downloaded YouTube SRT subtitles into a structured JSON corpus
(parsed/youtube_corpus.json).

Input:
  playlist                      — video ID + title per line
  raw/youtube/subs/*.srt        — SRT subtitle files

Output:
  parsed/youtube_corpus.json    — [{video_id, title, url, sub_lang, segments}]

Segment schema:
  index, start, end, text, translation (optional)

Usage:
  python3 scripts/youtube_parse.py
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYLIST_FILE = os.path.join(ROOT, "playlist")
SUBS_DIR = os.path.join(ROOT, "raw", "youtube", "subs")
OUT_FILE = os.path.join(ROOT, "parsed", "youtube_corpus.json")

YOUTUBE_URL = "https://www.youtube.com/watch?v={}"

SRT_TIMING_RE = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})$")


def load_playlist(path):
    titles = {}
    if not os.path.isfile(path):
        print(f"WARNING: playlist file not found at {path}", file=sys.stderr)
        return titles
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            video_id = parts[0]
            title = parts[1] if len(parts) > 1 else ""
            titles[video_id] = title
    return titles


def parse_srt(filepath):
    """Parse an SRT file into a list of segment dicts."""
    segments = []
    with open(filepath, encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        return segments

    blocks = content.split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        index = int(lines[0])

        m = SRT_TIMING_RE.match(lines[1])
        if not m:
            continue
        start, end = m.group(1), m.group(2)

        text_lines = []
        for line in lines[2:]:
            line = line.strip()
            if not line:
                continue
            text_lines.append(line)

        if not text_lines:
            continue

        segment = {
            "index": index,
            "start": start,
            "end": end,
            "text": text_lines[0],
        }
        if len(text_lines) >= 2:
            segment["translation"] = text_lines[1]

        if len(text_lines) >= 3:
            segment["extra"] = text_lines[2:]

        segments.append(segment)

    return segments


def main():
    print("Loading playlist...")
    titles = load_playlist(PLAYLIST_FILE)
    print(f"  {len(titles)} videos in playlist")

    entries = []
    srt_files = sorted(
        f for f in os.listdir(SUBS_DIR) if f.endswith(".srt")
    )

    for filename in srt_files:
        filepath = os.path.join(SUBS_DIR, filename)
        base = filename[:-4]  # strip .srt
        parts = base.rsplit(".", 1)
        video_id = parts[0]
        sub_lang = parts[1] if len(parts) > 1 else "??"

        segments = parse_srt(filepath)
        if not segments:
            print(f"  SKIP {filename}: no segments", file=sys.stderr)
            continue

        title = titles.get(video_id, "")
        entries.append({
            "video_id": video_id,
            "title": title,
            "url": YOUTUBE_URL.format(video_id),
            "sub_lang": sub_lang,
            "segments": segments,
        })

    entries.sort(key=lambda e: (e["video_id"], e["sub_lang"]))

    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)

    total_segments = sum(len(e["segments"]) for e in entries)
    segments_with_trans = sum(
        sum(1 for s in e["segments"] if "translation" in s)
        for e in entries
    )
    print(f"Done: {len(entries)} subtitle tracks, {total_segments} segments "
          f"({segments_with_trans} with translation)")
    print(f"Output: {OUT_FILE}")


if __name__ == "__main__":
    main()
