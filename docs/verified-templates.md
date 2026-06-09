# Verified Templates: the `.sctpl` bundle format

A `.sctpl` bundle is a workflow that ships with its own proof-of-execution. It packages
the workflow YAML, one or more recorded cassettes from a real run, and a checksummed
manifest into a single distributable file. Anyone can verify the proof locally - offline,
at $0, without trusting the author - before the workflow ever runs live.

```bash
sandcastle template verify lead-scoring-1.2.0.sctpl
#   lead-scoring v1.2.0  by amira-dev  (sandcastle 0.33.0)
#   PASS  cassettes/proof.cassette.json  6 step(s) replayed at $0
#   Verified: the bundled cassette(s) replay the workflow at $0.
```

## Why this works

A cassette records every model step's output keyed by a deterministic cache key:
`sha256(tenant : workflow_name : step_id : model : resolved_prompt)`. Replaying the
cassette against the bundled workflow re-derives those keys from scratch. That gives the
proof two independent tamper traps:

- **Tampered cassette** - the manifest's SHA-256 checksum of the cassette file breaks,
  and the cassette's own internal signature breaks. Detected before any replay.
- **Tampered workflow** - any change to a prompt, step id, model, or workflow name
  changes the cache keys, so the strict replay misses and fails. The provider is never
  called: in strict mode a miss aborts the step instead of falling through to a live call.

PASS means: this exact workflow, fed the bundled example inputs, replays the recorded
run to completion with zero provider calls and zero cost.

## Archive layout

A bundle is a zip archive (conventionally suffixed `.sctpl`):

```
manifest.json                   # metadata + sha256 checksums (schema below)
workflow.yaml                   # the workflow definition
cassettes/proof.cassette.json   # one or more recorded cassettes
```

## manifest.json schema (format_version 1)

```json
{
  "format": "sctpl",
  "format_version": 1,
  "name": "lead-scoring",
  "version": "1.2.0",
  "description": "Score inbound leads against your ICP",
  "author": "amira-dev",
  "license": "MIT",
  "sandcastle_version": "0.33.0",
  "created_at": "2026-06-09T00:00:00+00:00",
  "workflow": {
    "file": "workflow.yaml",
    "sha256": "<hex sha256 of workflow.yaml>"
  },
  "cassettes": [
    {
      "file": "cassettes/proof.cassette.json",
      "sha256": "<hex sha256 of the cassette file>",
      "step_count": 6,
      "recorded_cost_usd": 0.0312
    }
  ],
  "input_schema": { "required": ["lead"], "properties": { "lead": { "type": "string" } } },
  "example_inputs": { "lead": "Jane Doe, VP Eng at Acme" }
}
```

All top-level fields except `input_schema` and `example_inputs` are required.
`example_inputs` must be the exact inputs used when the cassette was recorded -
the replay resolves prompts from them, so different inputs produce different cache
keys and a FAIL. `created_at` defaults to the workflow file's last git commit date.

## Producing a bundle

```bash
# 1. Record a run into a cassette
sandcastle run --local --record proof.cassette.json my-workflow.yaml -i lead="Jane Doe"

# 2. Pack it - pack re-verifies the bundle and refuses to produce one that fails
sandcastle pack my-workflow.yaml \
    --cassette proof.cassette.json \
    --author you --bundle-version 1.0.0 \
    --input lead="Jane Doe"
```

`pack` accepts `--cassette` multiple times to bundle several proofs (e.g. different
input shapes), plus `--name`, `--description`, `--license`, `--created-at`, and
`--output`. A reproducible end-to-end example lives in
`scripts/build_example_bundle.py`, which builds the committed
`examples/templates/text-summarizer-1.0.0.sctpl`.

## Verifying and installing

```bash
sandcastle template verify bundle.sctpl            # PASS/FAIL per cassette, exit 0/1
sandcastle template verify bundle.sctpl --json     # machine-readable report

sandcastle template install bundle.sctpl           # verifies first; FAIL = no install
sandcastle template install https://.../bundle.sctpl --sha256 <hex>
```

Install places the workflow in the community templates directory - the same place
dashboard hub installs land - so the Lite wizard and dashboard surface it immediately.
The proof cassettes are kept alongside for re-verification. `--dir` overrides the
target, `--force` overrides a FAIL or an existing file.

## The index: `sandcastle template search`

Search reads a static JSON index from `settings.template_index_url`
(`TEMPLATE_INDEX_URL` env var). Any static file host works - GitHub raw, S3, a
plain web server:

```json
{
  "format_version": 1,
  "templates": [
    {
      "name": "text-summarizer",
      "version": "1.0.0",
      "description": "Executive summaries with parallel analysis",
      "author": "gizmax",
      "tags": ["text", "summarization"],
      "download_url": "https://raw.githubusercontent.com/gizmax/Sandcastle/main/examples/templates/text-summarizer-1.0.0.sctpl",
      "sha256": "<hex sha256 of the bundle file>"
    }
  ]
}
```

`sha256` is the checksum of the bundle file itself - pass it to
`template install --sha256` to pin the download. The repo ships a starter index at
`hub/template-index.json`.

## Security model

Bundles are untrusted input and are handled accordingly:

- **Safe extraction** - member names are validated against path traversal (zip-slip)
  and absolute paths; archive size, member count, and per-member decompressed size
  are capped. Nothing is extracted blindly to disk.
- **No code execution during verify** - the replay runs with `admin_trusted=False`,
  and workflows containing step types a cassette cannot cover (`code`, `http`,
  `browser`, ...) are rejected outright: phase 1 verifies prompt-step workflows.
- **Security scan** - the workflow YAML passes the same scanner that gates Community
  Hub installs (dangerous code patterns, SSRF URLs, secrets, YAML bombs).
- **Capped network** - bundle downloads are https-only with a timeout and a 10MB cap;
  the index fetch is capped at 5MB.
