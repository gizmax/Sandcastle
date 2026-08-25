# The markdown vault: memory an auditor can read

`MEMORY_BACKEND=filesystem` stores agent memory as markdown files in a git
repository instead of vectors in Qdrant.

```
MEMORY_BACKEND=filesystem
MEMORY_FS_ROOT=/srv/sandcastle/memory-vault   # optional; default <DATA_DIR>/memory-vault
MEMORY_FS_GIT=true                            # optional; default true
```

Nothing else changes. The default stays mem0+Qdrant; this is opt-in, and the
two do not share storage — switching backends does not migrate anything.

---

## 1. Why

A vector store answers "what is similar to this?" well. It answers "what did
the agent believe on 3 March, and who changed it?" not at all. A 384-float
embedding is not evidence.

That second question is the one the EU AI Act's record-keeping duties ask, the
one the Black Box exists to answer, and the one a customer asks after a bad
run. The vault trades retrieval quality for the ability to answer it:

```
~/.sandcastle/data/memory-vault/          # a git repo Sandcastle owns
  _shared/                                # scopes with no tenant prefix
    workflow-invoice-triage/
      INDEX.md                            # <= 1,500 tokens, regenerated
      billing.md                          # one file per subject
      deployment.md
      _attic/billing.md                   # superseded text, kept for the diff
```

`git log -p` over that directory is the record. `git diff` between two runs
shows exactly what the agent learned, changed its mind about, and forgot.

A domain file looks like this:

```markdown
---
domain: billing
entries: 2
forgotten: 1
keywords: [acme, invoice, ledger, net30]
revision: 7
scope: workflow:invoice-triage
updated: '2026-08-25T09:14:02+00:00'
---

# billing

### 2026-08-25 - 3df67bff86e5 (confirmed 4x)

Acme invoices arrive on the fifteenth and settle net-30.

<!-- meta: {"confirmations":4,"created":"...","id":"3df67bff86e5",...} -->

## Forgotten

- `9a1c0e2b7f41` ttl at 2026-08-20T00:00:00+00:00: A one-off note about ...
```

The prose is for the human. The `meta:` comment is for the machine — markdown
renderers hide it. The `## Forgotten` section means a deleted memory leaves a
visible hole rather than vanishing.

---

## 2. What it does on a write

Every write is routed to exactly one domain file by keyword overlap, then
classified against what that file already says:

| Overlap with the closest existing entry | What happens |
|---|---|
| **≥ 0.85** | **Confirm.** Bump the entry's counter. No new entry. |
| **0.40 – 0.85** | **Supersede.** Same id, new text; the old text goes to `_attic/`. |
| **< 0.40** | **Append.** A new entry with a new id. |

Overlap is the Szymkiewicz–Simpson coefficient over word sets — the same
heuristic `engine.memory.detect_conflicts` has carried since 0.44, which had no
caller until this backend.

The confirmation counter is the only evidence the vault has that a fact still
holds, so it is also the TTL veto (below).

### Forgetting

One consolidation pass at the end of a run, never mid-write:

1. **TTL.** Entries older than `MEMORY_MAX_AGE_DAYS` are dropped — *unless*
   they have been confirmed twice or more. Repetition across runs beats the
   clock.
2. **Domain merge.** Two files whose keyword sets overlap ≥ 0.60 become one.
   The lexicographically-first name wins, so reruns are no-ops.
3. **Attic purge.** Superseded text older than 180 days is removed.

Every one of the three leaves a tombstone in the file it emptied.

---

## 3. Where this is worse than vectors

Six of them, named. None are bugs; all are the cost of the format.

**1. No synonymy — and worse, false synonymy.** "The build is broken" and
"CI is red" mean the same thing and share no content words, so the vault files
them as unrelated facts. A vector store scores them as near-identical. Expect
duplicate entries saying the same thing in different words.

The failure runs the other way too, and it is the more dangerous direction.
The overlap measure counts *every* word over two characters, stopwords
included — `_STOPWORDS` filters keyword extraction, not overlap. Measured:

```
"The build is broken on main"  vs  "CI is red on the main branch"
  -> overlap 0.50 -> SUPERSEDE
```

Those two sentences share only "the" and "main", and the vault treats the
second as a *revision* of the first: same id, old text moved to the attic. The
attic makes that recoverable and the diff makes it visible — which is the whole
argument for this backend — but it is still a wrong merge that a vector store
would not make. Short entries are the worst case, because a couple of shared
function words is a large fraction of a small word set.

**2. English only, and silently degrading outside it.** Keyword extraction
filters `engine.memory._STOPWORDS`, a hand-written English list. Measured on
this implementation:

| Input | Extracted keywords |
|---|---|
| `Faktury od Acme chodi vzdy patnacteho v mesici.` | `faktury, acme, chodi, vzdy, patnacteho` |
| `Счета от Acme приходят пятнадцатого.` | `acme` |
| `請求書は毎月十五日に届きます。` | *(none)* |

Czech works but keeps its own stopwords, which then compete for the five
keyword slots. Cyrillic, Greek, Arabic and CJK produce no tokens at all — the
tokenizer is `[a-zA-Z0-9_]+` — so every such entry routes to the same fallback
domain, and a query in those scripts extracts no terms either. It does not
error: it silently falls back to "return everything, newest first". Verified:
three CJK/Cyrillic memories all landed in one `misc.md`, and a Japanese query
returned all three unranked. **The vault is usable in English and unreliable
outside it.**

**3. Lumpy scoring over very few keywords.** Routing compares at most five
extracted keywords against a domain's set. With five inputs, a score can only
be 0/5, 1/5, 2/5 … — there is no gradient near the 0.25 routing threshold, so
one word's presence flips a document between files. Vector cosine similarity is
continuous; this is a staircase.

**4. Linear scan.** Retrieval opens domain files and walks their entries. There
is no index in the database sense — `INDEX.md` is a *summary for humans* that
happens to be readable by the ranker. A query touches up to `max(8, limit)`
domain files and every entry inside them. At vault sizes this is
milliseconds; it does not become sublinear at any size, and a write reads every
domain file in the scope to recompute routing.

**5. Sticky routing errors compound.** Routing scores against a domain's
accumulated keyword set, which grows with every entry filed there. An entry
misfiled early adds its keywords to the wrong domain, which makes the next
similar entry more likely to be misfiled the same way. Nothing re-files an
entry after the fact. Consolidation merges whole domains; it never splits one.

**6. No graph.** mem0 can optionally build entity relations in Neo4j and answer
"who is connected to what". The vault has files and keywords. There is no
relation between entries beyond sharing a filename, and no traversal.

### And the cap

**A scope may hold at most 120 domain files, and hitting the cap is an error,
not a warning.** That is deliberate, and it is the price of auditability: past
roughly a hundred files a human stops reading a directory and starts grepping
it — at which point the vault has become a worse vector store instead of a
better audit log. A single domain file is capped at 32 KB for the same reason:
so one diff is reviewable. When the file cap is reached the oldest
least-confirmed entries are evicted to the attic with tombstones; when the
domain cap is reached the write fails and asks you to consolidate or narrow
the scope.

**Use vectors when retrieval quality is the product. Use the vault when the
record is.**

---

## 4. Git, honestly

Sandcastle owns a dedicated repository at the vault root — created with
`git init`, no remote, no user config, and hooks neutralised via
`core.hooksPath` on every invocation, not merely at init (a hook dropped into
`.git/hooks` later still never runs). Global and system git config are routed
to `/dev/null`. If git is missing the vault still works; it just loses the
history.

One commit per run per scope. The commit message names the scope and the run.

**Git history is not tamper-evident.** Anyone with write access to the vault
can `git commit --amend`, `git rebase`, or `git filter-repo` and leave no
trace. A git log proves *nothing* about what happened by itself — it is a
convenient record, not an attestation.

**Git history is also not GDPR-erasable in place.** Deleting a memory removes
it from `HEAD`; every prior commit still contains it. An erasure request
against the vault needs a history rewrite, not a delete.

The mitigation for the first problem is that every vault commit SHA is appended
to Sandcastle's audit hash chain as a `memory.vault.commit` event. That chain
*is* tamper-evident (`verify_audit_chain` recomputes every link), so a rewritten
vault no longer matches the SHA the chain recorded and the mismatch is
detectable. It does not prevent the rewrite; it makes it visible.

The mitigation for the second problem is procedural, not technical: rewrite the
history, then let the audit chain record that the pinned SHA no longer resolves.
The vault does not pretend erasure is free.

---

## 5. Concurrency

Writers take an exclusive `flock` on a per-scope lock file, then write each
file with the write-temp-fsync-rename pattern used by `LocalStorage`. Lock
order is fixed — scope lock first, git second, never the reverse.

Each domain file carries a `revision` in its frontmatter, checked
compare-and-swap style before a write; a mismatch triggers a re-read and a
re-apply (merge, don't fail), so a concurrent writer's entry survives.

**The flock is the guarantee; the CAS is not a substitute for it.** Measured
with the multiprocess test in `tests/test_memory_fs.py` (5 processes × 8 writes
contending on one domain file):

| | writes surviving |
|---|---|
| flock + CAS | 40 / 40 |
| CAS only | 17 / 40 |
| neither | 12 / 40 |

The CAS narrows the read-modify-write window; it cannot close it, because the
revision check is itself a read followed by a write. On a platform without
`fcntl`, or on a filesystem that ignores advisory locks, the vault logs a
warning and you should expect lost entries under concurrency.

That test is deliberately **multiprocess**. A threaded version passes for the
wrong reason: the GIL serialises the critical section and the lock is never
exercised.

---

## 6. Path safety

Scope ids are validated in `engine.memory` (`_VALID_SCOPE_RE` rejects every
`..` run and terminates with `\Z`). The vault does not rely on that. Every
scope is reduced to exactly two slugged path components, the final path is
resolved, and containment inside the tenant root is verified before any I/O.
A hostile scope like `tenant:../../root/workflow:x` produces a directory
*inside* the vault with a mangled name; it never escapes.

Tenants are separate top-level directories. Scopes with no tenant live under
`_shared`, and a tenant that would slug onto `_shared` is pushed aside so it
cannot collide with them.
