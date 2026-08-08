# Plan — P5 5.4 Risk Identification Generator

Status: **built and delivered 2026-08-08.** Migration `0023`.

Runtime-pipeline anchor: the first stage that *produces*. Everything in 5.1–5.3 exists so
this one could be short: the ledger holds what it says, the corpus is what it reads, and
the evidence format is what it cites with.

## What this is

A sweep over a project's document corpus that drafts cause-event-effect risk statements and
raises each as a **creation proposal**. No risk reaches the register without a human
disposition. Invariant 4 holds here by construction, not by discipline: the only write path
out of `services/risk_generate.py` is `proposal_ledger.propose`.

## The one thing it must do well

Produce citations that resolve. A reviewer's entire basis for trusting rows they did not
write is that the evidence points at something real and readable. Everything else in this
stage — the categorisation, the confidence, the wording — is a convenience the reviewer can
fix in seconds. A citation that resolves to nothing, or to the wrong paragraph, is a
suggestion that reads exactly like a good one and cannot be checked without leaving the
inbox.

So grounding is enforced twice: the system prompt asks for it, and `agents/risk_id.parse`
drops any candidate whose citations were not in the pack it was shown. The instruction
makes compliance likely; the check makes it true.

## Decisions locked

- **A sweep, not a query.** You cannot BM25 for risks nobody has thought of yet. The corpus
  is walked window by window and every chunk is read exactly once. `services/evidence.py`'s
  `search()` is *not* called by this generator; it remains the right interface for the
  query-shaped ones (a probability suggestion asks about a *named* risk). The `kind`
  strings are still imported from `evidence.py` so refs cannot drift out of resolvability.
  **The most contestable decision in the delivery.**

- **Windows never span documents.** They could, and it would mean fewer calls. A risk
  grounded half in a geotechnical report and half in a contract is one the reviewer has to
  reconstruct from two citations with no shared context, and a mixed pack invites exactly
  that.

- **Deduplication belongs to the generator, not the ledger.** Creation proposals carry
  `target_id IS NULL` and are exempt from the one-pending-per-field index, so nothing in
  5.1 stops a rerun doubling the inbox. The ledger cannot tell that two draft risks written
  in different words are the same risk. The generator can.

- **Two thresholds, asymmetric.** ≥0.75 token overlap against the register suppresses and
  reports; 0.45–0.75 keeps the candidate and attaches the matching risk as a second
  citation. A false suppression is invisible and permanent — a real risk that never reaches
  anyone, with nothing recording that it was found. A false pass is one inbox row rejected
  in four seconds. The band between the numbers is where "possibly the same thing" lives,
  and it resolves into a citation rather than into a suppression or a silence.

- **Nothing is suppressed silently.** Every drop lands on the run with its reason and, where
  there was one, the raw item. "Fourteen offered, three already in the register" is a
  result; fourteen becoming eleven with no explanation is a bug report waiting to be filed.

- **Identification does not score.** No probability, no impact, no status in the creation
  payload (`CREATABLE_RISK_FIELDS`). Those come from an elicitation with the people who own
  the work. A probability shipped inside a creation payload gets accepted as a side effect
  of accepting the risk statement: one click, two decisions, one of them invisible.

- **`app/agents/` is pure.** No DB, no network, no clock, no randomness — the boundary
  `app/sim/` and `app/ingest/` already hold. It matters more here than anywhere: the claims
  this platform makes about its AI features are all decided by code in that package, and
  all of them are checkable with a string and a frozen dataclass. The moment prompt
  construction and response admission need a session to exercise, they stop being
  properties anyone verifies.

- **`LLM_PROVIDER` and `LLM_MODEL` default to empty and refuse.** A fake reached by accident
  fills a reviewer's inbox with invented proposals indistinguishable from real ones. A live
  provider reached by accident spends money on a misconfiguration. Neither is a default
  worth having.

- **Raw `httpx`, not the vendor SDK.** One call shape, four wire fields, no retries, no
  streaming. The deciding argument is the run transcript: a thin client makes it literally
  the request and the response rather than an SDK's rendering of them.

- **Runs are auditable, not replayable, and say so.** No temperature makes a model
  deterministic across time and deployments. `generation_run` stores prompt version,
  provider, model, temperature, a `pack_sha256` of the extracts sent, and the raw responses.
  Deliberately not called a seed — a column named `seed` on a row that cannot use one is
  the failure mode worth avoiding.

- **Truncation is surfaced, never repaired.** `stop_reason == "max_tokens"` is recorded per
  window. A repaired JSON array is one whose contents nobody can attest to, and a quietly
  accepted cut-off array turns a window that found nine risks into one that found four.

- **`created_target_id` is separate from `target_id`.** Back-filling the latter would move
  accepted creations into the partial unique index's scope and destroy the only signal
  saying a proposal made a row rather than changed one.

## Shape

```
app/llm/            provider seam — types, fake, anthropic, registry
app/agents/         pure — types, risk_id (prompt + parse), dedupe
services/risk_generate.py     orchestration; only write is propose()
services/generation_dispatch.py  eager | celery seam, mirrors sim_dispatch
tasks/generation.py           the worker task
api/routes/generation.py      start, list, read, batch
models/generation.py          GenerationRun, append-only
```

## Open

- **Cancel.** Twenty windows is twenty paid calls and there is no way to stop one halfway.
  Needs a fifth status, a revoke path, and a decision about a worker mid-call. Own delivery,
  the way `simulation_run` took its cancel in 0018. Highest-value follow-up.
- **UI.** 5.1–5.4 are API-only. The corpus view and proposal inbox are still unbuilt — but
  there are now real generated rows to design the inbox against, which was the whole
  argument for deferring it.
- **Cost ceiling in currency.** Only `GENERATION_MAX_WINDOWS` exists. Token counts are
  recorded per run, so the data to build one is there.
- **Embeddings.** Still no vector column. Nothing in this generator needs one — a sweep
  reads everything — but the query-shaped generators will.
