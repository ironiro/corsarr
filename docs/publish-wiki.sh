#!/usr/bin/env bash
# Copy docs/*.md into the repository's GitHub wiki.
#
# One-time: on GitHub open the repo → Wiki → "Create the first page" → save (any content).
# Then, from the repo root:  docs/publish-wiki.sh
# Wiki links work without ".md", so "[x](Page.md#a)" becomes "[x](Page#a)" on the way.
set -euo pipefail

cd "$(dirname "$0")/.."
REMOTE="$(git remote get-url origin)"
# Commit as the identity of this repository, not the global one (which may hold a real name and address)
NAME="$(git config user.name)"
EMAIL="$(git config user.email)"
WIKI="${REMOTE%.git}.wiki.git"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet "$WIKI" "$TMP" || {
    echo "Wiki repository not found. On GitHub, create the first page in the Wiki tab first." >&2
    exit 1
}
for page in docs/*.md; do
    sed -E 's/\]\(([A-Za-z_][A-Za-z0-9_-]*)\.md(#[^)]*)?\)/](\1\2)/g' "$page" > "$TMP/$(basename "$page")"
done
rm -rf "$TMP/images" && cp -R docs/images "$TMP/images"  # screenshots referenced as images/…
cd "$TMP"
git add -A
if git diff --cached --quiet; then
    echo "Wiki is already up to date."
    exit 0
fi
git -c user.name="$NAME" -c user.email="$EMAIL" commit --quiet -m "Update wiki from docs/"
git push --quiet
echo "Wiki updated: ${REMOTE%.git}/wiki"
