#!/usr/bin/env bash
# deploy-site.sh — publish site/ to sandcastle-ai.eu over FTP.
#
# The marketing site is NOT deployed from CI: .github/workflows/pages.yml only
# builds the dashboard. Uploading files one at a time by hand is how llms.txt,
# llms-full.txt and og-image.jpeg ended up 404ing on the live domain while
# sitting in the repo. This walks the whole directory instead.
#
# Usage:
#   export SANDCASTLE_FTP_USER=... SANDCASTLE_FTP_PASS=...
#   bash scripts/deploy-site.sh --dry-run     # list what would be uploaded
#   bash scripts/deploy-site.sh               # upload everything
#   bash scripts/deploy-site.sh --only llms.txt sitemap.xml
#
# Credentials come from the environment and must never be committed.
set -euo pipefail

# Upload over FTPS with a verified certificate.
#
# ftp.sandcastle-ai.eu resolves to 62.109.154.139 (Webglobe, dw181), but the
# server presents a *.nameserver.sk certificate, so connecting by that name
# fails verification and tempts you into --insecure. dw181.nameserver.sk is
# the same host under a name the certificate actually covers, so TLS verifies
# properly and the password never crosses the network in the clear.
FTP_HOST="${SANDCASTLE_FTP_HOST:-dw181.nameserver.sk}"
FTP_ROOT="${SANDCASTLE_FTP_ROOT:-/public_html}"
SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/site"

DRY_RUN=0
ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --only) shift; while [[ $# -gt 0 && "$1" != --* ]]; do ONLY+=("$1"); shift; done ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ $DRY_RUN -eq 0 ]]; then
  : "${SANDCASTLE_FTP_USER:?set SANDCASTLE_FTP_USER}"
  : "${SANDCASTLE_FTP_PASS:?set SANDCASTLE_FTP_PASS}"
fi

# Files that live in site/ but must never be published.
EXCLUDES=(
  ".DS_Store"
  "launch/"          # internal runbooks: Product Hunt copy, FTP + GSC notes
)

should_skip() {
  local rel="$1"
  for ex in "${EXCLUDES[@]}"; do
    [[ "$rel" == *"$ex"* ]] && return 0
  done
  return 1
}

if [[ ${#ONLY[@]} -gt 0 ]]; then
  FILES=("${ONLY[@]}")
else
  FILES=()
  while IFS= read -r f; do FILES+=("$f"); done < <(cd "$SITE_DIR" && find . -type f | sed 's|^\./||' | sort)
fi

uploaded=0; skipped=0; failed=0
for rel in "${FILES[@]}"; do
  src="$SITE_DIR/$rel"
  if [[ ! -f "$src" ]]; then
    echo "  MISSING  $rel" >&2; failed=$((failed+1)); continue
  fi
  if should_skip "$rel"; then
    echo "  skip     $rel"; skipped=$((skipped+1)); continue
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    printf "  would upload  %-46s %s\n" "$rel" "$(wc -c <"$src" | tr -d ' ') B"
    uploaded=$((uploaded+1)); continue
  fi
  # --ftp-create-dirs makes nested paths (compare/, hub/, eu-ai-act/) work.
  # --ssl-reqd refuses to fall back to plaintext if STARTTLS is unavailable.
  if curl -sS --fail --ssl-reqd --ftp-create-dirs -T "$src" \
       --user "$SANDCASTLE_FTP_USER:$SANDCASTLE_FTP_PASS" \
       "ftp://$FTP_HOST$FTP_ROOT/$rel" >/dev/null; then
    echo "  uploaded $rel"; uploaded=$((uploaded+1))
  else
    echo "  FAILED   $rel" >&2; failed=$((failed+1))
  fi
done

echo
echo "uploaded: $uploaded  skipped: $skipped  failed: $failed"
[[ $failed -eq 0 ]] || exit 1

if [[ $DRY_RUN -eq 0 ]]; then
  echo
  echo "Verify a few of the files that were missing before:"
  for u in / /compare/ /llms.txt /og-image.jpeg /sitemap.xml; do
    printf "  %-18s %s\n" "$u" "$(curl -s -o /dev/null -w '%{http_code}' "https://sandcastle-ai.eu$u")"
  done
fi
