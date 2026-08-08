# APPLY — P5 5.3 Evidence Service

## Prerequisites

**Apply 5.1 and 5.2 first, in that order.** This zip's copies of `app/core/errors.py`,
`app/api/errors.py` and `app/main.py` carry all three deliveries' edits; unpacking over a
tree missing either earlier one leaves them importing modules that are not there.

**No migration.** 5.3 is read-only over what 5.1 and 5.2 built. Schema head stays at 0022.

## Commit message

```
feat: evidence service, the one retrieval interface every generator calls

BM25 over three substrates: the document corpus, the register as a reference
class, and schedule activity names. app/retrieval/ is pure and takes tokens
rather than text, so the tokenizer stays in services/ with its lexicon and the
risk-to-activity suggester and this share one vocabulary.

Abstention is on term overlap, not on a score floor. BM25 scores are unbounded
and corpus-relative, so an absolute threshold means one thing over forty chunks
and something else over four thousand; a hit must instead match query terms
carrying a real share of the query's IDF mass. A term present in every candidate
is worth nothing at all.

Every hit reports which query terms caused it. Every search reports what it
searched and how large each corpus was, so "no evidence" over forty chunks and
over four thousand read as the different claims they are.

resolve() ships alongside search(): the ledger stores refs and nothing else, and
without resolution a proposal accepted eight months ago cites a string.
```

## Apply

```bash
unzip -o p5-5.3-evidence-service.zip -d /path/to/Risk-Platform
cd /path/to/Risk-Platform/backend
python -m pytest -q            # expect 1089 passed, 3 skipped
python -m ruff check app/retrieval app/services/evidence.py app/api/routes/evidence.py \
                     tests/test_bm25.py tests/test_evidence_service.py
```

No new dependencies. No `make migrate`.

## Files

New:
- `backend/app/retrieval/__init__.py`, `backend/app/retrieval/bm25.py`
- `backend/app/services/evidence.py`
- `backend/app/api/routes/evidence.py`
- `backend/tests/test_bm25.py` (16 tests)
- `backend/tests/test_evidence_service.py` (25 tests)
- `claude/plans/evidence-service.md`

Modified (each carries 5.1's and 5.2's edits too):
- `backend/app/core/errors.py` — `EvidenceRefUnresolvable` (append only)
- `backend/app/api/errors.py` — one handler + registration
- `backend/app/main.py` — mount the router

No frontend files touched.

## Design decisions — flagged

**Revertible.**

1. **History is searched across the whole hierarchy by default.** A reference class limited
   to the current project is empty exactly when it matters most. Every result carries its
   scope and is flagged `from_other_scope`, which is what makes the breadth acceptable
   rather than a leak. `history_across_scopes=false` narrows it per call; flipping the
   default is one keyword.

2. **A term present in every candidate is worth zero IDF.** Lucene's published form gives
   it a small positive weight — enough that an all-universal query returns the whole corpus
   in arbitrary order, which is worse than nothing because it looks like an answer. The
   rule is exact (`df >= n`), not a majority threshold picked to make an example work.

3. **`MIN_IDF_SHARE = 0.15`.** The abstention threshold. A judgement, not a measurement,
   and the first number to move if retrieval returns noise or abstains too readily. `K1`
   and `B` are left at the published defaults deliberately: a hand-picked value with no
   evaluation behind it is worse than the standard one because it looks deliberate.

4. **Cross-source sorting is by IDF share, then raw score.** Raw score alone ranks a
   source's population rather than its relevance, because a BM25 score over four hundred
   activity names and one over four thousand chunks are not on one scale.

5. **Newest schedule version only.** Older versions describe the same work under names
   since corrected, and a suggestion citing a superseded activity code is worse than one
   citing nothing.

6. **`MAX_CANDIDATES = 20_000` per source, per search.** Declared on the response via
   `truncated` rather than silently applied. Goes away when retrieval moves to Postgres
   full-text or pgvector.

## Verification run

Fresh `git clone --depth 1` of `main` at `f362022`; 5.1, 5.2, then this zip unpacked over
it; pinned deps from `requirements.txt` + `requirements-dev.txt`.

- `python -m pytest -q` — **1089 passed, 3 skipped** (1048 after 5.2, + 41 new)
- `ruff check` — clean on every new and modified file. Tree-wide `ruff check .` still
  reports the same three pre-existing F401s on `main`, none from this delivery.
- No migration to render or execute; schema head unchanged at 0022.

## Known gaps

- **Retrieval quality is unmeasured.** No labelled query set exists, so `MIN_IDF_SHARE`,
  `K1`, `B` and 5.2's chunk targets are reasoned defaults rather than tuned values. A small
  hand-labelled set is the prerequisite for moving any of them, and is worth building
  before 5.4 rather than after.
- **IDF is rebuilt in-process on every search.** Correct, and fine at current corpus sizes.
- **`activity` results report the requested scope rather than a real one.** Activities hang
  off a schedule version, not a scope; reporting anything else would be a claim the adapter
  cannot make, so `from_other_scope` stays false for them.
- **Nothing has run these queries against Postgres.** They are ordinary SQLAlchemy selects
  with no dialect-specific constructs.
