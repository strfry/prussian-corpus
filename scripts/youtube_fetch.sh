#!/usr/bin/env bash
set -euo pipefail
#
# playlist erzeugen/aktualisieren:
#   yt-dlp --flat-playlist --print "%(id)s %(title)s" \
#     "https://www.youtube.com/@prusiskataliwidasna/videos" > playlist
#
# Video-Titel stammen aus dem playlist-File und werden von youtube_parse.py
# daraus in youtube_corpus.json übernommen.

SRC="playlist"
OUTDIR="raw/youtube/subs"
LANGS=(en lv lt de pl ru prg)

mkdir -p "$OUTDIR"

while IFS=' ' read -r id title; do
    URL="https://www.youtube.com/watch?v=$id"

    # Skip video if any subtitle file already exists.
    if compgen -G "$OUTDIR/$id.*.srt" > /dev/null; then
        echo "SKIP $id – already fetched"
        continue
    fi

    IFS=,; lang_list="${LANGS[*]}"; IFS=$' \t\n'
    echo "=== $id: $title ==="

    yt-dlp --write-subs --sub-langs "$lang_list" --skip-download \
           --convert-subs srt --sub-format srt \
           --output "$OUTDIR/$id" \
           "$URL" || true

    sleep 1
done < "$SRC"
