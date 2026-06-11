#!/usr/bin/env bash
#
# verify-release.sh — verify the authenticity of a Sandcastle release archive.
#
# Usage:
#   verify-release.sh <path-to-archive> [SHA256SUMS.txt]
#
# What it does:
#   1. Computes the SHA-256 of the given archive (portable: prefers sha256sum,
#      falls back to `shasum -a 256`).
#   2. If a SHA256SUMS.txt file is supplied, checks that the archive's hash
#      matches the line for that file. Prints a clear PASS/FAIL and exits
#      non-zero on a mismatch.
#   3. If `cosign` is installed AND a matching `.sig`/`.pem` pair is present
#      next to the SHA256SUMS file, additionally verifies the keyless
#      (Sigstore) signature. The signature step is optional: its absence is
#      reported as "skipped", never as a failure.
#
# Exit codes:
#   0  everything that ran passed
#   1  usage error / missing input
#   2  checksum mismatch
#   3  cosign signature verification failed
#
set -euo pipefail

# ---- argument parsing -------------------------------------------------------

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <path-to-archive> [SHA256SUMS.txt]" >&2
  exit 1
fi

ARCHIVE="$1"
SUMS="${2:-}"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "ERROR: archive not found: $ARCHIVE" >&2
  exit 1
fi

# ---- compute the archive's SHA-256 (portable) -------------------------------

# Emits just the lowercase hex digest for the given file.
compute_sha256() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    echo "ERROR: neither 'sha256sum' nor 'shasum' is available" >&2
    exit 1
  fi
}

ACTUAL_HASH="$(compute_sha256 "$ARCHIVE")"
echo "Archive : $ARCHIVE"
echo "SHA-256 : $ACTUAL_HASH"

# If no checksum file was given, we are done after printing the digest.
if [[ -z "$SUMS" ]]; then
  echo
  echo "No SHA256SUMS.txt provided — printed digest only (nothing to compare)."
  echo "Re-run with the published SHA256SUMS.txt to verify authenticity."
  exit 0
fi

if [[ ! -f "$SUMS" ]]; then
  echo "ERROR: checksum file not found: $SUMS" >&2
  exit 1
fi

# ---- checksum verification --------------------------------------------------

# Look up the expected hash by matching the archive's basename in SHA256SUMS.txt.
# SHA256SUMS lines look like: "<hash>  <filename>" (two spaces, GNU coreutils style).
ARCHIVE_BASENAME="$(basename "$ARCHIVE")"
EXPECTED_HASH="$(awk -v f="$ARCHIVE_BASENAME" \
  '{ name = $2; sub(/^\*/, "", name); if (name == f) { print $1; exit } }' \
  "$SUMS")"

if [[ -z "$EXPECTED_HASH" ]]; then
  echo
  echo "FAIL: no entry for '$ARCHIVE_BASENAME' found in $SUMS" >&2
  exit 2
fi

# Case-insensitive compare to be safe across tools.
if [[ "$(printf '%s' "$ACTUAL_HASH" | tr 'A-Z' 'a-z')" \
   == "$(printf '%s' "$EXPECTED_HASH" | tr 'A-Z' 'a-z')" ]]; then
  echo "Expected: $EXPECTED_HASH"
  echo "PASS: checksum matches the entry in $SUMS"
else
  echo "Expected: $EXPECTED_HASH"
  echo "Actual  : $ACTUAL_HASH"
  echo "FAIL: checksum does NOT match — do not trust this download." >&2
  exit 2
fi

# ---- optional cosign signature verification ---------------------------------

# Only attempt this when cosign exists AND a .sig + .pem pair sits next to SUMS.
SIG="${SUMS}.sig"
CERT="${SUMS}.pem"

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign: not installed — signature check skipped."
  exit 0
fi

if [[ ! -f "$SIG" || ! -f "$CERT" ]]; then
  echo "cosign: no ${SIG##*/} / ${CERT##*/} found next to checksums — signature check skipped."
  exit 0
fi

echo
echo "cosign: verifying keyless (Sigstore) signature over $(basename "$SUMS") ..."

# Keyless verification: the signing identity is the release workflow, issued by
# GitHub's OIDC provider. Adjust the identity regexp if the repo path changes.
if cosign verify-blob \
    --certificate "$CERT" \
    --signature "$SIG" \
    --certificate-identity-regexp 'https://github.com/gizmax/Sandcastle/.*' \
    --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
    "$SUMS"; then
  echo "PASS: cosign signature verified."
else
  echo "FAIL: cosign signature verification failed." >&2
  exit 3
fi
