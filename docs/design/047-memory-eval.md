# 047 — `sandcastle memory eval`: grading the store, not the retrieval

**Status:** implemented in 0.47, workstream 1
**Module:** `src/sandcastle/engine/memory_eval.py`
**CLI:** `sandcastle memory eval <scope>`
**Tests:** `tests/test_memory_eval.py`

---

## 1. The problem this scores

The 330-day ops report circulating on X had a memory layer with textbook
retrieval numbers and a store that was lying: three of ten memories were
stale, nothing had ever been deleted. Recall@k said nothing about either.
The verdict was "not memory, a log with good manners."

Retrieval quality answers *can I find what I stored*. It cannot answer
*should that still be there*. Those are different questions and only the
second one catches a store that has quietly become a liability.

The 0.46 vault already records exactly the metadata the second question
needs — a confirmation counter, timestamps per entry, a supersede chain
with an attic, tombstones with reasons, a Forgotten ledger. Nothing read
any of it back. This is the reader.

**Design stance: every score is a division of two counted things, and both
counts are in the report.** There is no learned weighting, no opaque
composite whose parts you cannot see, and no number in the output that a
reader cannot recompute by hand from the numbers printed next to it. Where
a threshold was invented rather than derived, §7 names it.

---

## 2. Data sources — what the vault actually records

Anchors are `src/sandcastle/engine/memory_fs.py` at `121e922`. Every field
below was read out of the parser, not assumed.

### 2.1 Live entries — `parse_domain`, memory_fs.py:254-302

`parse_domain(text) -> (frontmatter, entries, tombstones)`. Each entry dict
(built at memory_fs.py:289-300) carries exactly:

| field | type | written by | meaning |
|---|---|---|---|
| `id` | str (12 hex) | memory_fs.py:1181 | stable across supersede |
| `text` | str | prose above the meta comment | **current** text only |
| `created` | ISO ts | memory_fs.py:1183 | first append |
| `updated` | ISO ts | memory_fs.py:1157 / :1173 | last confirm **or** supersede |
| `confirmations` | int | memory_fs.py:1156 | times re-asserted |
| `run_id` | str | memory_fs.py:1162 | last run that touched it |
| `keywords`, `tags` | list[str] | enrichment | routing set |

There is **no** per-entry TTL and **no** `last_read`/access counter. Both
absences constrain the formulas below; see §6.

### 2.2 Frontmatter — `render_domain`, memory_fs.py:326-336

`domain`, `scope`, `revision`, `entries`, `updated`, `keywords`,
`forgotten`. `revision` is a monotone write counter per domain file, which
makes it the only durable record of *write volume* the vault keeps — see
§4.2 for why the forgetting metric does not use it.

### 2.3 Tombstones — `_TOMB_RE`, memory_fs.py:118; parsed at :265-271

`{id, reason, at, excerpt}`, rendered into the `## Forgotten` section
(memory_fs.py:361-380). Four reasons exist in the whole codebase:

| reason | emitted at | means |
|---|---|---|
| `ttl` | memory_fs.py:1405 | expired, veto did not save it |
| `size-cap` | memory_fs.py:1266 | evicted so the 32 KB file fits |
| `deleted` | memory_fs.py:1307 | explicit `delete()` |
| `merged-into-<target>` | memory_fs.py:1475 | a **domain file**, not an entry |

Tombstones are never purged. That makes them the durable removal record.

### 2.4 Attic — `append_attic`, memory_fs.py:758-796

`<!-- attic: {...} -->` per record: the whole entry dict plus `at` and
`reason` ∈ {`superseded` (memory_fs.py:1167), `size-cap` (:1270),
`deleted` (:1322), `ttl` (:1412)}. Supersede records additionally carry
`overlap`.

`superseded` records appear **only** in the attic — a supersede leaves no
tombstone, because the id stays live. That makes the attic the only source
of supersede-chain depth, and it is why the two metrics that need it
(contradiction pressure, retrieval sanity) degrade when the attic is
purged at 180 days (memory_fs.py:97, `_purge_attic_locked` :1500-1539).
The report states its own attic horizon rather than pretending depth is
lifetime-complete.

### 2.5 Policy constants read, not copied

`CONFIRMATIONS_VETO = 2` (:94), `MAX_DOMAINS_PER_SCOPE = 120` (:75),
`MAX_DOMAIN_FILE_BYTES = 32768` (:78), `ATTIC_MAX_AGE_DAYS = 180` (:97),
`SUPERSEDE_OVERLAP = 0.40` (:82). All are imported from `memory_fs`, never
re-declared, so a policy change moves the score with it.

TTL comes from `settings.memory_max_age_days` (config.py:277, default 90),
the same value `_resolve_max_age` (memory_fs.py:1541) feeds to
consolidation. The scorer and the forgetter therefore agree by construction.

---

## 3. Scoring shape

Four metrics. Each is scored on **0.0–1.0 where 1.0 is healthy**, or `None`
when the scope contains nothing the metric can be computed from. Each
carries its own counted parts and a one-line plain-language verdict.

Scores are computed **per domain and per scope**. The scope-level number
is computed over the scope's entries directly, not by averaging domain
scores — averaging would let a one-entry domain outvote a hundred-entry one.

`overall` is the unweighted mean of the non-`None` metric scores, and the
components are printed beside it. It exists so a CI job has one number to
threshold; it is not the interesting part of the report.

---

## 4. The four metrics

### 4.1 Staleness

The vault's TTL is scope-wide, not per entry (§2.1), so "expected useful
life" is `MEMORY_MAX_AGE_DAYS` for every entry. Age is measured from
`updated` falling back to `created` — deliberately the *same* field
`_consolidate_sync` uses at memory_fs.py:1394, so the scorer never calls an
entry stale that the forgetter considers fresh.

Per entry:

```
age_days   = now - (updated or created)
expired    = age_days > ttl_days
vetoed     = confirmations >= CONFIRMATIONS_VETO      # memory_fs.py:1396
```

That splits the past-TTL population in two, and the split is the point:

- **`expired_pending`** = `expired and not vetoed`. Consolidation will drop
  these at the next run end. A *transient* state — unless it is not, in
  which case consolidation is not running, which the verdict says out loud.
- **`immortal`** = `expired and vetoed`. The confirmations veto
  (memory_fs.py:1396) means the vault will **never** re-examine these. Two
  confirmations in March buy permanent residency. This is the population
  that produces "3 of these 10 memories are stale" and it is the number
  this metric exists to surface.

```
stale_share      = (expired_pending + immortal) / live_entries
staleness_score  = 1 - stale_share
```

Flat, unweighted, hand-checkable. `immortal` is the worse of the two and
gets its own line in the verdict rather than a weight, because a weight
would make the score unrecomputable from the printed parts.

Worst offenders are ranked by `staleness_ratio = age_days / ttl_days`,
named with entry id, domain, age, confirmations and the veto flag.

**`ttl_days <= 0`** (expiry disabled, config.py:277 allows 0): score is
`None`, not 0.0, with the verdict *"TTL is disabled: nothing in this scope
can expire, so staleness is unmeasurable and forgetting is manual."* An
unmeasurable metric is reported as unmeasurable; scoring it 0 would be
inventing evidence. It drops out of `overall`.

### 4.2 Forgetting health

Does the store ever shrink. Counted from tombstones (§2.3), which are the
durable record:

```
removed  = tombstones with reason in {ttl, size-cap, deleted}
merges   = tombstones with reason merged-into-*        # counted, not scored
written  = live_entries + removed
forget_rate = removed / written                         (0 if written == 0)
```

`written` reconstructs "entries ever appended" from state the vault still
holds: a superseded entry keeps its id and stays live, so supersede is not
an append and is not double-counted; a removed entry left a tombstone. A
domain merge moves files, not facts, so it is reported separately and never
counted as forgetting — merging two files is not remembering less.

```
forgetting_score = min(1.0, forget_rate / TARGET_FORGET_RATE)   # 0.10, invented
forgetting_score = 0.0  when removed == 0 and live_entries > 0
```

The hard zero is the requirement from the critique: **a store that only
grows scores worst**, with the verdict naming it — *"never forgotten
anything: N written, 0 removed; this is an append-only log."*

The target rate is a floor test, not a curve: it asks whether the store
forgets *at all, at a token rate*, and saturates immediately above it. It
does not claim 10% is correct — see §7.

Two things reported beside the score, neither folded into it:

- **`superseded`** (from the attic): a store that supersedes briskly but
  removes nothing is *updating* without *forgetting*. Different disease,
  different fix; the verdict distinguishes them.
- **`consolidation_lag`**: true when `expired_pending > 0`, i.e. entries
  are sitting past TTL that consolidation should already have taken. It
  cross-links staleness to forgetting: a zero forget rate with a large
  pending pile is a broken end-of-run hook, not a policy choice.

**Known undercount, stated in the report:** `_delete_all_sync`
(memory_fs.py:1332) unlinks whole domain files, taking their tombstones
with them. A scope that was erased and refilled looks append-only. There is
no counter-evidence left on disk; the honest response is to say so, which
the module does via a `caveats` list rather than guessing.

### 4.3 Contradiction pressure

Supersede depth comes from attic records with `reason == "superseded"`
grouped by id (§2.4):

```
depth(id)  = |{attic records: reason == superseded, id == id}|
contested  = live entries with depth >= 2
churn_share        = contested / live_entries
contradiction_score = 1 - churn_share
```

Depth 1 is ordinary maintenance — a fact was updated once. Depth ≥ 2 is
*"we keep changing our mind about this"*, which is the pressure worth
naming. Worst offenders are the deepest chains, by id.

Reported beside it, not blended:

- `confirmations_total`, `supersedes_total`, and
  `confirm_supersede_ratio = confirmations_total / max(1, supersedes_total)`.
  High: facts are being re-asserted consistently. Low: they are being
  rewritten. It is a ratio of two printed counts and is diagnostic, not
  scored — there is no defensible target value for it.
- **Caps.** `domain_fill = len(rendered_bytes) / MAX_DOMAIN_FILE_BYTES` per
  domain, and `scope_fill = domain_count / MAX_DOMAINS_PER_SCOPE`. Anything
  at or above `NEAR_CAP = 0.80` (invented, §7) is listed by name. Hitting
  either cap is an error in the vault (memory_fs.py:1140, :1277), not a
  warning, so approaching one is an operational fact the report must
  surface — but it is a capacity fact, not a contradiction, so it gets its
  own verdict line and stays out of the score.

  The file-cap number is measured by re-rendering with `render_domain`
  (memory_fs.py:304) rather than stat-ing the file, because that is what
  `_enforce_size_cap` measures (memory_fs.py:1244-1251). Note the tombstone
  interaction: tombstones render into the file (memory_fs.py:361-380) and
  therefore count against the 32 KB cap, so a domain that forgets a lot
  eventually evicts live entries to make room for the record of what it
  forgot. `domain_fill` sees that; a live-entry count would not.

### 4.4 Retrieval sanity

The one metric that must go through the backend's own retrieval. It uses
`FilesystemMemoryBackend.load(scope_id, query, limit)` — the `MemoryBackend`
Protocol method (memory.py:662) — and nothing else. No bespoke ranker, no
reaching into `_load_sync` internals: if the public path is broken, the
score must show it broken.

Probe set: every live entry with `depth >= 1` whose most recent superseded
text is still in the attic.

**The probe is the superseded text, not the current text.** This was
corrected during implementation, and the reason is worth keeping: querying
an entry with its own current text is the friendliest possible question and
passes by construction. `_overlap` (memory_fs.py:220) is the
Szymkiewicz–Simpson coefficient, so an identical word set scores exactly
1.0 and nothing can outrank it; and any *other* entry that ties at 1.0 must
have a word set that is a subset of the query, which makes its overlap with
the current text 1.0 too — so the "stale twin ahead" test can never fire.
A metric that cannot fail is not a metric.

Querying with what the entry *used to say* is the question where a stale
answer is dangerous. For each probe:

```
results      = load(scope, latest_superseded_text, limit)
unreachable  = entry id absent from results
stale_ahead  = a live result ranked above it whose text overlaps the old
               text by >= SUPERSEDE_OVERLAP and overlaps the old text more
               than it overlaps the current text
clean        = not unreachable and not stale_ahead
retrieval_score = clean / probes_run
```

Two failure modes, both real and both reachable:

1. **`unreachable`.** Supersede replaces the entry's keyword set wholesale
   (memory_fs.py:1174) and retrieval ranks on word overlap over at most
   `max(8, limit)` domain files (memory_fs.py:1046). A rewrite that shares
   no words with what it replaced puts the fact out of reach of its own
   history: ask the old question and nothing connects you to the revision.
2. **`stale_ahead`.** The vault's documented no-synonymy weakness
   (`docs/memory-filesystem-vault.md` §3.1) means a paraphrase of the *old*
   fact can live on as a separate entry in another domain. Ask the old
   question and that twin comes back first — yesterday's answer, while the
   vault's own diff looks clean. This is the exact "log with good manners"
   shape, and it is what this probe exists for.

**Honest limit, stated in the report:** the vault's `load` reads live
domain files only and never opens `_attic/`, so atticked text can only ever
return under some *other* live entry's id, which is precisely
`stale_ahead`. It can never return under the superseded entry's own id;
that half of the check is a regression guard against a future change that
starts indexing the attic, not a discriminator today.

Probes are capped at `max_probes` (default 50, deterministic by sorted id)
because each probe is a full linear scan; the report prints
`probes_run` / `probes_total` so a truncated sample is never mistaken for a
complete one. `None` score when there is no supersede history at all, with
the verdict saying so.

---

## 5. Report shape

Four dataclasses, all with `to_dict()`; `MemoryEvalReport.to_dict()` is what
`--json` prints.

```
Metric      {name, score: float|None, verdict, parts{}, worst[], caveats[]}
EntryGrade  {id, domain, created, updated, age_days, confirmations,
             expired, vetoed, staleness_ratio, supersede_depth}
DomainGrade {domain, live_entries, fill, near_cap,
             staleness, forgetting, contradiction, retrieval, parts{}}
MemoryEvalReport
            scope_id, backend, tenant, scope_path, generated_at, ttl_days,
            totals{live_entries, domains, tombstones, attic_records,
                   superseded, confirmations},
            staleness / forgetting / contradiction / retrieval : Metric|None,
            domains[DomainGrade], entries[EntryGrade],
            overall: float|None, components{name: score|None},
            not_measurable[{metric, reason}],   # non-vault backends
            caveats[]
```

One `Metric` type for all four rather than four shapes: the parts differ,
the contract does not. Per metric:

| metric | `parts` keys that the score divides | `worst` holds |
|---|---|---|
| staleness | `live_entries`, `immortal`, `expired_pending`, `stale`, `stale_share` | oldest expired entries with `ttl_ratio`, `confirmations`, `immortal` |
| forgetting | `removed`, `written`, `forget_rate`, plus `merges`, `superseded`, `tombstones_by_reason`, `consolidation_lag` | — (the offenders are already gone) |
| contradiction | `live_entries`, `contested`, `churn_share`, plus `supersedes_total`, `confirmations_total`, `confirm_supersede_ratio`, `domains_near_cap`, `scope_fill` | deepest supersede chains by id |
| retrieval | `probes_run`, `clean`, `unreachable`, `stale_ahead`, `probes_total` | failing probes with `failure` and a `detail` naming the twin |

Every `parts` dict holds the integers the score divides. `verdict` is one
sentence of plain English naming the counts — no adjectives without numbers.
A metric that could not be computed carries `score: None` and a verdict that
says why; it never carries `0.0` as a stand-in for "unknown".

---

## 6. What is honestly not measurable on mem0

The `_Mem0Backend` adapter (memory.py:683-770) exposes five Protocol
methods and normalises results to `{id, memory, metadata, created_at,
updated_at}` (memory.py:718-726). That is the whole surface. The scorer
does not reach around the Protocol into mem0's own database — a metric that
requires bypassing the interface is not a property of the backend seam, and
faking it would be exactly the dishonesty this release is about.

**Measurable:** entry count and per-entry age. `load(scope, "", limit)`
goes to `get_all` (memory.py:707-709) and returns `created_at`/`updated_at`,
so `expired = age > MEMORY_MAX_AGE_DAYS` is computable against the same TTL
`apply_decay` (memory.py:485) uses.

That is one number, and it means something weaker than it does on the
vault. On mem0 the TTL is a **read filter**, not a forgetter:
`apply_decay` hides expired entries from a caller that asks for decay and
leaves them in storage forever. So mem0's "expired" count is *"entries a
decay-aware read would hide"*, never *"entries the store will remove."*
The report says that in the verdict rather than reusing the vault's wording.

**Not measurable, and why:**

| metric | why not |
|---|---|
| staleness split (`immortal` vs `expired_pending`) | no confirmations counter anywhere in the stack — the veto has no analogue and metadata carries only what `save_memory` put there (memory.py:936-940) |
| forgetting health | no tombstone, no ledger, no attic. A mem0 delete leaves nothing behind this seam can see; a store that deleted half its contents is indistinguishable from one that never wrote them |
| contradiction pressure | no supersede chain **through the Protocol** — see the correction below |
| domain / cap pressure | mem0 has no domain concept and no caps; genuinely absent, not merely unexposed |
| retrieval sanity | needs a probe set of *known-superseded* entries. Without supersede history there is no probe set — the query would run, but nothing would make its answer a check |

### Correction, from checking the installed mem0 rather than assuming

The version actually installed here is **mem0 2.0.18**, and it is richer than
the first draft of this document assumed:

- `Memory.history(memory_id)` exists and returns a memory's
  ADD/UPDATE/DELETE history — that *is* a supersede chain.
- `Memory.add()` and `Memory.update()` take an `expiration_date`, and
  `get_all(show_expired=...)` filters on it — that *is* a native TTL with
  expiry semantics.

Neither reaches `_Mem0Backend`, which implements the five Protocol methods
and nothing else, and Sandcastle never sets `expiration_date`. So the honest
statement is not "mem0 cannot record this" but **"mem0 records some of it
and our adapter does not surface it."** The report says it that way. This
module still refuses to reach around the Protocol into `mem0.Memory` to get
at it: a metric that needs a hole punched through the backend seam is not a
property of the seam. Widening `_Mem0Backend` is follow-up 7 in §9.

### And a live bug found while checking

`_Mem0Backend.load` (memory.py:697-726) calls
`client.search(query, user_id=..., limit=...)` and
`client.get_all(user_id=...)`. mem0 2.0.18 **rejects** `user_id` as a
top-level kwarg on both — `_reject_top_level_entity_params`
(mem0/memory/main.py:165, called at :1282 for `get_all` and :1436 for
`search`) raises `ValueError: Top-level entity parameters {'user_id'} are
not supported ... Use filters={'user_id': '...'} instead`. Verified by
running it.

`load_memories` (memory.py:864-867) catches the exception, logs a warning
and returns `[]`. So on the installed mem0, **every memory read returns
empty and the caller is told nothing** — a silent failure of exactly the
kind 0.47's other workstream is about. It is pre-existing and outside this
workstream's blast radius, so it is reported, not fixed: follow-up 8 in §9.

The consequence for this module is benign and already handled:
`_evaluate_limited` catches the read failure and emits a report whose
`not_measurable` section says `everything: backend read failed: <exc>`
rather than a report of zero entries that looks like a healthy empty scope.

The CLI prints all of this as a section headed `not measurable on this
backend`, each item with its reason. It is a feature: an operator reading it
learns what switching backends costs them, which is the actual decision in
front of them.

If a future backend exposes none of it, the section says exactly that and
the report carries `overall = None`.

---

## 7. Invented decisions

Everything here was chosen by the implementer, not derived from the vault
or from the roadmap. Listed so a reviewer can argue with each one.

1. **`TARGET_FORGET_RATE = 0.10`.** The saturation point of the forgetting
   score. No literature, no measurement — a floor test that asks "does this
   store forget at all." Anything above 10% of ever-written entries removed
   scores 1.0. Changing it moves every forgetting score, so it is a module
   constant with this paragraph next to it.
2. **`NEAR_CAP = 0.80`.** When a domain file or a scope is "approaching"
   a hard cap. Chosen so a report warns with roughly a fifth of headroom
   left.
3. **Depth ≥ 2 is contested, depth 1 is not.** A single rewrite is normal
   maintenance. The line has to go somewhere; it went here.
4. **Scores are `1 - bad_share`, flat and unweighted.** `immortal` is worse
   than `expired_pending` and is *not* weighted higher, because a weight
   makes the printed parts insufficient to recompute the score. The
   severity difference lives in the verdict text instead.
5. **`overall` is an unweighted mean of non-`None` components.** Any
   weighting would be a claim about relative importance that nothing
   supports.
6. **`None` rather than `0.0` for unmeasurable metrics**, and exclusion
   from `overall`. Scoring an unknown as a failure is inventing evidence.
7. **Scope-level scores are computed over entries, not averaged over
   domains.** Averaging domain scores lets a one-entry domain outvote a
   hundred-entry one.
8. **`max_probes = 50`, `probe_limit = 10`.** Cost control for retrieval
   sanity; both are parameters, and the report prints the sample size.
   Probes are the *most recent* superseded text per id, one query per
   entry, rather than one per link in the chain.
9. **Domain merges are counted but not scored as forgetting.** Moving two
   files into one does not mean the store remembers less.
10. **Age uses `updated`, not `created`.** It matches consolidation
    (memory_fs.py:1394). The cost: a confirm bumps `updated`
    (memory_fs.py:1157), so re-asserting an old fact makes it young again
    by both the forgetter's reckoning and the scorer's. That is the vault's
    policy, and the scorer's job is to grade the vault, not to overrule it.
11. **Reading the vault directly for the counting metrics.** Staleness,
    forgetting and contradiction parse the markdown through `memory_fs`'s
    own `parse_domain` / `_VaultGit`-free helpers rather than going through
    `load()`, because `load()` returns a *ranked, truncated* view and would
    make the counts depend on the ranker. Retrieval sanity is the one
    metric that must go through `load()`, and does.

---

## 8. Concurrency

**Scoring is read-only and takes no lock.** It opens domain files, attic
files and `INDEX.md` for reading and writes nothing, so the multiprocess
safety work in `_FileLock` (memory_fs.py:468-532) is not needed here and is
deliberately not used: taking the scope's exclusive `flock` to *read* would
block a live writer for the duration of a full scope scan, which is a worse
trade than a slightly-torn score.

The consequence, stated rather than hidden: a scope being written during a
scan can produce a report that mixes revisions of different files. Every
individual file is read atomically (writers use write-temp-fsync-rename,
memory_fs.py:450-467), so no file is ever seen half-written; the report can
only be inconsistent *between* files. For a grade — "3 of 10 are stale" —
that is acceptable. For anything that must be exact, run it against a
quiesced scope or a `git` checkout of the vault at a known SHA.

`generated_at` is stamped into the report so two reports are comparable.

---

## 9. Follow-ups (not done here; would need `memory_fs.py` changes this
workstream is not allowed to make)

1. **No public accessor for a scope's on-disk contents.** The scorer uses
   `_FilesystemVault` internals (`scope_dir`, `list_domains`, `read_domain`,
   `attic_path`) via the backend's `_vault` attribute. `parse_domain`,
   `render_domain` and `split_scope` are public; the vault object's methods
   are not. A small public read-only façade on `FilesystemMemoryBackend`
   (`iter_domains()`, `read_scope()`) would let this module stop touching a
   private attribute. Worked around read-only for now.
2. **`_all_scope_ids` (memory_fs.py:1551) is private** and is the only way
   to enumerate scopes for a `memory eval --all`. Same façade would cover it.
3. **No per-entry TTL.** Everything ages at one scope-wide rate. A
   `ttl_days` field in the entry meta would make staleness a per-fact
   judgement instead of a scope-wide one — "this deploy token expires in
   30 days, this company name does not."
4. **No read/access record.** The vault knows when an entry was written and
   confirmed, never when it was last *useful*. An access counter would turn
   staleness from "old" into "old and unused", which is the stronger signal.
5. **Attic purge caps supersede-chain depth at 180 days.** Contradiction
   pressure therefore measures recent churn, not lifetime churn. A durable
   `depth` counter in the entry meta, incremented on supersede, would fix it
   for one integer per entry.
6. **`delete_all` erases the tombstones** with the domain files
   (memory_fs.py:1332), so forgetting history does not survive a scope
   erase. A scope-level ledger outside the domain files would.
7. **`_Mem0Backend` could expose more than it does.** mem0 2.0.18 has
   `Memory.history(memory_id)` and `expiration_date`/`show_expired`. Adding
   an optional `history(memory_id)` to the backend seam (Protocol-optional,
   `hasattr`-guarded here) would make contradiction pressure and retrieval
   sanity measurable on mem0 too, and would shrink the honesty section from
   five items to two. Not done here: it changes the backend seam, which is
   a wider blast radius than a scorer.
8. **`_Mem0Backend.load` is broken against mem0 2.0.18** (see §6). Fixing it
   means passing `filters={"user_id": scope_id}` and `top_k` instead of
   `user_id=`/`limit=`, plus a test that fails on the current call shape.
   Deliberately not done in this workstream: it changes the behaviour of the
   *default* memory backend for every caller, and it deserves its own PR
   with its own regression test rather than riding along inside a scorer.
