# Reconstructed design notes: filesystem memory backend

The full design doc (scratchpad `045/D-filesystem-memory-design.md`, ~1051
lines) was lost to a tmp sweep. Verified findings from its author's final
report. Anchors are 0.45-era; re-verify. The seam-extraction half ALREADY
LANDED in 0.45 — follow the code in `engine/memory.py`, not the doc's plan
for it.

- **Layout:** `INDEX.md` capped at **1,500 tokens**, measured with the repo's
  own `estimate_tokens` (compaction.py:65; 4 chars/token = 6,000 chars),
  enforced by regeneration + row-dropping + an overflow line. Hard caps:
  **120 domains/scope (error, not warning)**, **32 KB/domain file**.
  Frontmatter pattern copyable from `agent_skills.py:270` (`_split_frontmatter`).
- **Write policy:** deterministic keyword routing → overlap **≥0.85 CONFIRM**
  (bump counter, no new entry), **0.40-0.85 SUPERSEDE** (same id, old text to
  attic), **<0.40 append** — reusing the currently-dead `detect_conflicts`
  (memory.py:508). Forgetting = TTL with a confirmations veto, domain merge
  with tombstones, attic purge — all in ONE end-of-run consolidation pass.
- **Retrieval honesty (ships as user docs):** six places keyword retrieval is
  worse than vectors — no synonymy, no cross-lingual (`_STOPWORDS` is
  English-only, memory.py:131), lumpy Jaccard over ≤5 keywords, linear scan,
  sticky routing errors, no graph. The 120-domain cap is explicitly the price
  of auditability.
- **Git:** Sandcastle owns a dedicated repo at the vault root, one commit per
  run per scope, hooks neutralised, degrades gracefully if git is missing.
  Honest counter-argument (goes in docs): git history is neither
  tamper-evident nor GDPR-erasable in place — mitigated by pinning each commit
  SHA into the audit hash chain (audit.py:116).
- **Concurrency:** flock + `os.replace` (pattern at storage.py:91-105) +
  revision CAS (mirroring memory_stores.py:174) + merge-don't-fail + fixed
  lock ordering. The concurrency test MUST be **multiprocess** — a threaded
  version can be GIL-serialised into a false pass.
- **Zero-dep is real:** core resolves to 90 packages vs 121 with the `memory`
  extra; pyyaml is already core.
- **Path safety:** 0.45 closed `_VALID_SCOPE_RE` (every `..` run rejected, `\Z`
  terminator, tenant sanitiser), but the backend still does defence in depth:
  resolve the final path and verify containment in the tenant root before I/O.
