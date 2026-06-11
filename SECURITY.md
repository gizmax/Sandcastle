# Security Policy

Sandcastle is an open-source workflow orchestrator. This document explains how to
report security issues, why some antivirus scanners flag the source archive as a
false positive, and how to verify that a download you obtained is authentic.

## Reporting a vulnerability

We practice coordinated disclosure. If you believe you have found a security
vulnerability in Sandcastle, please report it privately so we can fix it before
details become public.

- **Email:** [security@sandcastle-ai.eu](mailto:security@sandcastle-ai.eu)
- **GitHub:** Open a [private security advisory](https://github.com/gizmax/Sandcastle/security/advisories/new)
  ("Report a vulnerability" under the **Security** tab).

**Please do not open a public GitHub issue, pull request, or discussion for a
security bug.** Public disclosure before a fix is available puts every user at risk.

What to expect:

- We **acknowledge** new reports within **24 hours**.
- For confirmed **critical** issues we aim to ship a fix within **72 hours**.
- We will keep you updated on progress and coordinate a disclosure timeline with
  you. With your permission we are happy to credit you once a fix has shipped.

When reporting, please include enough detail to reproduce the issue: affected
version or commit, a description of the impact, and a minimal proof of concept if
you have one.

## Antivirus / false positives

Some heuristic antivirus scanners flag the Sandcastle source archive. The most
common is **CleanMyMac / Moonlock**, which reports the download as
`Riskware/HiddenCode`; **Windows Defender** and a handful of other engines
occasionally raise a similar generic "Riskware" verdict.

**This is a false positive.** Here is why, stated factually:

- **Sandcastle's job is to run code.** It is a workflow orchestrator: code steps
  execute inside a restricted sandbox, and an agent runner spawns subprocesses to
  drive tools and runtimes. Any tool whose legitimate purpose is to execute code or
  launch processes matches the same heuristics scanners use to detect "riskware."
  This signature reflects *what the software does*, not that it is malicious.
- **The test suite contains attack-string fixtures.** Files such as
  `tests/test_security_pentest.py` and `tests/test_sandbox_security.py` include
  SSRF targets (e.g. cloud metadata endpoints), path-traversal strings, and Python
  sandbox-escape probes. These are **negative tests**: they exist to *verify that
  Sandcastle's security guards block them*. They are inert test inputs, not live
  exploits, and they are never executed against a real target.
- **The repository ships none of the usual malware indicators.** Verifiably:
  - **No compiled binaries** — the source archive is plain text source code.
  - **No obfuscated or packed code** — everything is human-readable.
  - **No install-time code execution** — there is no `npm` `postinstall`/`preinstall`
    hook and no `setup.py` running code at install time (packaging is declarative
    via `pyproject.toml`).
  - **No network beacons** — nothing phones home on download, install, or import.

### What you should do

- **It is safe to whitelist Sandcastle** in your scanner (add an exclusion for the
  download or the cloned repository folder).
- **Do not click the scanner's "Remove" / "Delete" action.** Because the flag
  points at ordinary repository files (often test fixtures), removing them would
  delete parts of the project, not protect you.
- **Verify authenticity instead of trusting the scanner's verdict.** Use the
  published checksums and signatures described below to confirm your download is the
  genuine, unmodified release.
- **Report persistent false positives to the AV vendor** so the signature can be
  corrected. If a flag affects a verified-authentic Sandcastle release, please also
  let us know at [security@sandcastle-ai.eu](mailto:security@sandcastle-ai.eu).

## Verifying your download

Every GitHub Release attaches a `SHA256SUMS.txt` file listing the SHA-256 hash of
each source artifact (`sandcastle-<tag>.tar.gz` and `sandcastle-<tag>.zip`). When
[cosign](https://github.com/sigstore/cosign) keyless signing is enabled, the release
also attaches `SHA256SUMS.txt.sig` and `SHA256SUMS.txt.pem` (a Sigstore signature
and certificate).

### Quick check (checksum)

Download the archive **and** `SHA256SUMS.txt` from the same release, then:

```bash
# Linux
sha256sum -c SHA256SUMS.txt --ignore-missing

# macOS (compute the hash and compare it to the matching line in SHA256SUMS.txt)
shasum -a 256 sandcastle-<tag>.tar.gz
grep sandcastle-<tag>.tar.gz SHA256SUMS.txt
```

The two hashes must match exactly. If they do not, **do not use the download** —
re-fetch it from the official release page and report a mismatch.

### Helper script

A portable helper is provided at
[`scripts/verify-release.sh`](scripts/verify-release.sh):

```bash
scripts/verify-release.sh sandcastle-<tag>.tar.gz SHA256SUMS.txt
```

It computes the archive's SHA-256, checks it against `SHA256SUMS.txt`, and — if
`cosign` is installed and a `.sig`/`.pem` pair is present — also verifies the
Sigstore signature. It exits non-zero on any mismatch.

### Signature check (optional, cosign)

If you have `cosign` installed, you can verify the keyless Sigstore signature over
the checksum file:

```bash
cosign verify-blob \
  --certificate SHA256SUMS.txt.pem \
  --signature SHA256SUMS.txt.sig \
  --certificate-identity-regexp 'https://github.com/gizmax/Sandcastle/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  SHA256SUMS.txt
```

A successful verification confirms the checksums were signed by Sandcastle's
GitHub Actions release workflow.
