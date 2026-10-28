#!/usr/bin/env bash
# Nightly cron wrapper for twanksta_nightly.py.
#
# raw/twanksta must lie inside a git checkout of the data repo, on a branch
# (strfry/wirdeins; raw/twanksta is a symlink to ../../wirdeins/twanksta). Only that directory is checked, staged and committed,
# so the data repo can hold other things too. Commits only when the run succeeded
# and changed something; rebases onto and pushes to the remote if there is one.
# Everything goes to stdout so cron's MAILTO delivers it.
#
# crontab:  17 3 * * *  /path/to/corpus/scripts/twanksta_nightly.sh
# Extra arguments are passed to twanksta_nightly.py (for testing).
set -euo pipefail

CORPUS="$(cd "$(dirname "$0")/.." && pwd)"
RAW="${TWANKSTA_RAW:-$CORPUS/raw/twanksta}"
PYTHON="${PYTHON:-$CORPUS/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
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

git add -A -- .
if git diff --cached --quiet -- .; then
    exit 0
fi
# commit message = the language-neutral block before the first blank line
git commit -q -m "$(sed '/^$/q' <<<"$summary" | sed '/^$/d')" -- .
if git remote | grep -q .; then
    git pull -q --rebase
    git push -q
fi
