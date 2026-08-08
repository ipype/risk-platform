# Plan — P5 5.3 Evidence Service

Status: **built and delivered 2026-08-08.** No migration — 5.3 is read-only over what 5.1
and 5.2 built.

Runtime-pipeline anchor: substrate **E**. "One retrieval interface. Every suggestion calls
this. No evidence → abstain with null, never zero."

## The contract

A generator cannot cite what it did not retrieve. `Evidence.as_ref()` produces exactly the
`{kind, ref, excerpt}` shape the ledger's `evidence_refs` requires, so retrieval output
goes straight into a proposal rather than a reference being composed by hand — which is the
only way a citation gets written for something that was never found.

`search()` returning nothing is a *result*, not a failure. The ledger's CHECK already
refuses an unevidenced proposal; stating the same rule here, one layer earlier, is what
lets a generator act on it instead of being rejected by it.

## Decisions locked

- **BM25 before vectors, and not only because no provider is chosen.** Lexical retrieval is
  the right first adapter regardless: no model, deterministic, and *explainable* — every
  hit reports which query terms it matched. That is what lets a reviewer judge a citation
  rather than trust it. Vectors go behind the same interface later and blend; none of this
  is thrown away.
- **`app/retrieval/` is pure and takes tokens, not text.** The tokenizer and lexicon stay
  in `services/mapping_*`, so the risk-to-activity suggester and the evidence service share
  one vocabulary instead of drifting into two, and the dependency arrow stays pointed the
  right way.
- **Abstention is on term overlap, not on a score floor.** BM25 scores are unbounded and
  corpus-relative — an absolute threshold means one thing over forty chunks and another
  over four thousand. A hit must match query terms carrying ≥ `MIN_IDF_SHARE` (0.15) of the
  query's IDF mass. Matching only "permit" out of "permit consent dewatering delay" has
  said nothing.
- **A term present in *every* candidate is worth zero.** Lucene's `+1`-inside-the-log form
  keeps IDF positive (Robertson's original goes negative, which would let a document rank
  higher for *lacking* a query term), but it still gives a universal term a small positive
  weight — enough that an all-universal query would return the whole corpus in arbitrary
  order. Exact rule (`df >= n`), not a majority threshold picked to make an example work.
- **Sorting across sources is by IDF share first, not raw score.** A BM25 score over four
  hundred activity names and one over four thousand chunks are not on the same scale;
  interleaving by raw score ranks a source's population rather than its relevance.
- **History is searched across the whole hierarchy by default.** A reference class limited
  to the current project is empty exactly when it matters most — on a project that has not
  run a workshop yet — and "four other projects carried this" is the most useful thing this
  substrate says. Every result carries its scope and is flagged `from_other_scope`, which
  is what makes the breadth acceptable rather than a leak. `history_across_scopes=false`
  narrows it.
- **Newest schedule version only.** Older versions describe the same work under names since
  corrected; retrieving both lets a suggestion cite an activity code that no longer exists
  in the schedule anyone is looking at.
- **IDF is rebuilt per search over a capped candidate set (`MAX_CANDIDATES = 20_000`).**
  Rarity is only meaningful against the corpus being searched, so a cached global IDF would
  rank against a population the reviewer is not looking at. Both the rebuild and the cap are
  approximations and both are declared on the response (`corpus_sizes`, `truncated`) rather
  than hidden.
- **`resolve()` ships with `search()`.** The ledger stores refs and nothing else; without
  resolution a proposal accepted eight months ago cites a string. Documents being withdrawn
  rather than deleted (5.2) is what makes this hold.
- **`cost_model` is named in its absence.** There is no CBS table. A source list that simply
  omitted it would read as an oversight rather than a gap.

## Substrates

| source | reads | scope |
|---|---|---|
| `doc_chunk` | `document_chunk` joined to active `document` | subtree |
| `risk` | `risk` as a reference class | whole tree by default, flagged |
| `activity` | `schedule_activity` of the newest version | via the version |
| `cost_model` | — | not built |

## API

`GET /evidence/search?q=&scope_id=&source=&limit=&history_across_scopes=`,
`GET /evidence/resolve?ref=`, `GET /evidence/sources`.

## Deliberately out of scope

- Embeddings and vector search. The interface is shaped to take them.
- Query expansion, synonyms beyond the shared lexicon, phrase matching.
- Any generator. 5.4.
- Retrieval UI.

## Open items

- **Retrieval quality is unmeasured.** There is no labelled set, so `MIN_IDF_SHARE`, `K1`,
  `B` and the chunk targets from 5.2 are all reasoned defaults rather than tuned values. A
  small hand-labelled query set is the prerequisite for moving any of them.
- **IDF is rebuilt per search in-process.** Fine at the current corpus size, and the thing
  that goes when retrieval moves to Postgres full-text or pgvector.
- **`activity` results report the requested scope rather than a real one.** Activities hang
  off a schedule version, not a scope; reporting anything else would be a claim the adapter
  cannot make.
- **No Postgres execution of the source queries** — they are ordinary SQLAlchemy selects
  with no dialect-specific constructs, but nothing has run them against Postgres.
