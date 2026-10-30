#!/usr/bin/env bash
# Nightly cron wrapper for twanksta_nightly.py.
#
# raw/twanksta must lie inside a git checkout of the data repo, on a branch
# (strfry/wirdeins; raw/twanksta is a symlink to ../../wirdeins/twanksta). Only that directory is checked, staged and committed,
# so the data repo can hold other things too. Commits only when the run succeeded
# and changed something; rebases onto and pushes to the remote if there is one.
# Everything goes to stdout so cron's MAILTO delivers it.
#
# After the scrape the raw HTML is converted to NVH in the same commit:
# nvh/full.nvh (scripts/twanksta_to_dmlex.py, every attested form) and
# nvh/lean.nvh (fst gen/compress_forms.py, only what the grammar does not
# generate; needs the fst checkout with built atoms). state/nvh.json records
# which corpus/fst revisions produced them, since a code change alone also
# changes them. A failed conversion keeps the previous files and is reported.
# The compressor works entry by entry but its memory grows with the input
# (~1.3 GB in one go), so it runs on chunks of 1000 entries; the concatenated
# result is byte-identical to a single run. Even a chunk peaks at ~400-560 MB,
# more than the Uberspace has left next to flexsrv/Lexonomy, so lean.nvh is
# only built with TWANKSTA_LEAN=1.
#
# crontab:  17 3 * * *  /path/to/corpus/scripts/twanksta_nightly.sh
# Extra arguments are passed to twanksta_nightly.py (for testing).
set -euo pipefail

CORPUS="$(cd "$(dirname "$0")/.." && pwd)"
RAW="${TWANKSTA_RAW:-$CORPUS/raw/twanksta}"
PYTHON="${PYTHON:-$CORPUS/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
FST="${TWANKSTA_FST:-$CORPUS/../fst}"
RUNDIR="$CORPUS/state"   # lock + stderr log, outside the data repo
mkdir -p "$RUNDIR"

exec 9>"$RUNDIR/twanksta_nightly.lock"
flock -n 9 || { echo "twanksta nightly: previous run still active, skipping"; exit 0; }

cd "$RAW"
if ! git symbolic-ref -q HEAD >/dev/null; then
    echo "twanksta nightly: data repo is on a detached HEAD (make sync?), refusing to run"
    exit 1
fi
if [ -n "$(git status --porcelain -- .)" ]; then
    echo "twanksta nightly: $RAW has uncommitted changes, refusing to run"
    git status --short -- . | head -20
    exit 1
fi

summary="$("$PYTHON" "$CORPUS/scripts/twanksta_nightly.py" --raw "$RAW" "$@" 2>"$RUNDIR/twanksta_nightly.log")" || {
    status=$?
    echo "$summary"
    echo "--- stderr (tail) ---"
    tail -30 "$RUNDIR/twanksta_nightly.log"
    exit $status
}
echo "$summary"

compress_chunked() {   # in out
    local tmp f
    tmp="$(mktemp -d)"
    awk -v n=1000 -v d="$tmp" '/^[^ ]/ { if (c++ % n == 0) f++ } { print > sprintf("%s/%04d.nvh", d, f) }' "$1"
    for f in "$tmp"/*.nvh; do
        (cd "$FST" && uv run --frozen python gen/compress_forms.py --in "$f" --out "$f.lean" \
            --no-stats --review-out "$tmp/review.tsv") >/dev/null || { rm -rf "$tmp"; return 1; }
    done
    cat "$tmp"/*.nvh.lean > "$2"
    rm -rf "$tmp"
}
rev() { git -C "$1" describe --always --dirty --abbrev=7 2>/dev/null || echo unknown; }
mkdir -p nvh
if "$PYTHON" "$CORPUS/scripts/twanksta_to_dmlex.py" --raw "$RAW" --out nvh/full.nvh.new \
        --no-save-ids >/dev/null 2>>"$RUNDIR/twanksta_nightly.log"; then
    mv nvh/full.nvh.new nvh/full.nvh
else
    rm -f nvh/full.nvh.new
    echo "WARNING: NVH conversion failed, nvh/ left as it was (see $RUNDIR/twanksta_nightly.log)"
fi
if [ -z "${TWANKSTA_LEAN:-}" ]; then
    :
elif [ ! -d "$FST/gen" ]; then
    echo "WARNING: no fst checkout at $FST, nvh/lean.nvh not updated"
elif compress_chunked nvh/full.nvh nvh/lean.nvh.new 2>>"$RUNDIR/twanksta_nightly.log"; then
    mv nvh/lean.nvh.new nvh/lean.nvh
else
    rm -f nvh/lean.nvh.new
    echo "WARNING: NVH compression failed, nvh/lean.nvh left as it was (see $RUNDIR/twanksta_nightly.log)"
fi
if [ -n "$(git status --porcelain -- nvh)" ]; then
    printf '{\n "corpus": "%s",\n "fst": "%s"\n}\n' "$(rev "$CORPUS")" "$(rev "$FST")" > state/nvh.json
fi

git add -A -- .
if git diff --cached --quiet -- .; then
    exit 0
fi
# commit message = the language-neutral block before the first blank line
block="$(sed '/^$/q' <<<"$summary" | sed '/^$/d')"
git commit -q -m "$(head -1 <<<"$block")" -m "$(tail -n +2 <<<"$block")" -- .
if git remote | grep -q .; then
    git pull -q --rebase
    git push -q
fi
