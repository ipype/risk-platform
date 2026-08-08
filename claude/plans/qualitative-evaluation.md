# Plan — P5 5.5 Qualitative Evaluation Generator

Status: **built and delivered 2026-08-08.** Migration `0024`.

Runtime-pipeline anchor: **S4**. The first *query-shaped* generator, and the first caller
of the evidence service `search()` that 5.3 built for exactly this.

## What this is

A pass over the risks already on a project's register that proposes a probability level and
per-area impact levels, each grounded in evidence retrieved for that risk. Two proposals
per risk — `probability` and `impact_scores` — landing in the ledger for human disposition.

No new applier. `APPLIABLE_RISK_FIELDS` already carried both fields and the existing risk
applier already re-derives `impact` (worst case across areas) and the band. The stage is
short because 5.1–5.4 were built in the order they were.

## The one thing it must do well

**Refuse to score without a basis.** A probability looks the same whether it was reasoned
from a document or produced to fill a field, and it does not stay decorative: it multiplies
into the matrix, the matrix drives triage, and triage decides which risks get an expensive
quantitative elicitation. An invented 4 is an invisible decision about where the whole
analysis spends its attention.

Enforced three times, on purpose:

1. **Retrieval abstaining means no call is made.** The subject is recorded as skipped and
   the model is never asked. A version that asked anyway and let the parser catch the
   answer would pass every other test in the suite while spending money to invent numbers.
2. **A citation that was not in the pack is not evidence.** `parse` drops the whole
   assessment if nothing it cited was shown to it.
3. **An area with no reason behind it is omitted, not guessed.** The prompt says so and the
   parser keeps partial answers, so omission costs nothing.

## Decisions locked

- **The scale is read from `matrix_config` and sent in full**, including each area's own
  descriptors. A five-point cost scale means different money on a €40M water main than on a
  €4B rail programme; a prompt with a hard-coded 5×5 produces scores wrong by a constant
  with no symptom. Out-of-scale answers are refused rather than clamped — clamping a 7 to 5
  turns a misread contract into the highest score there is.

- **The model never supplies an overall impact.** Worst-case-across-areas is this register's
  rule and lives in `models/matrix.overall_impact`. A model that supplies an overall is
  quietly proposing a different aggregation rule.

- **Two proposals per risk, not one.** A reviewer who agrees the cost impact is a 4 and
  thinks it less likely than the model does should not have to reject the impacts to say
  so. Separate field paths also give each half its own supersession through the ledger's
  one-pending-per-field index on a rerun — which creation proposals, carrying no target,
  never got. **This is the first generator to exercise that index at all.**

- **A field a person set is never re-scored. Not a flag.** There is no
  `include_assessed`. Proposing against a judgement made in a workshop is the generator
  arguing with the people who were in the room, and the ledger has no way to express that
  which a reviewer reads as anything but noise. An analyst wanting a second opinion clears
  the field — one action that says what it means.

- **Values a person did set are merged into the payload and declared on its face.**
  `impact_scores` is one JSON column the applier writes whole, so a proposal holding only
  the model's areas would erase theirs on acceptance. The rationale names which areas were
  carried through and which accepting would actually change. **This is the failure this
  platform would have been least able to explain afterwards.**

- **The subject is filtered out of its own evidence.** A risk searching the register
  matches itself perfectly on every term and takes the top slot, and a suggestion citing
  the risk it is about reads in an inbox exactly like a well-evidenced one.

- **The evidence search is scoped to the project, and history overrides it.** Documents and
  activities from a sibling project are not evidence about this one; the register is, and
  `history_across_scopes` is what makes a project that has not run a workshop yet
  serviceable at all. Passing the scope anyway is what makes `from_other_scope` meaningful
  — without a scope to compare against, the evidence service cannot tell a sibling's
  precedent from this project's own history, and an unlabelled precedent reads as the
  latter. Cross-scope hits are labelled in the prompt *and* in the stored excerpt.

- **Register comparables are declared for what they are.** Other analysts' judgements, not
  observed frequencies. In the system prompt, in every rationale. S11 is what changes that;
  until then the claim would be false and invisible.

- **`skipped` is a separate column from `dropped`.** A drop says the model was asked and
  its answer was refused; a skip says it was never asked. A pass that skipped thirty risks
  for want of evidence and one that asked about thirty and refused every answer produce the
  same proposal count and mean opposite things — one is an empty corpus, the other a broken
  prompt.

- **`subject_ids` is named for subjects, not risks.** Quantitative elicitation (S5) and
  risk-to-activity mapping (S6) are the same shape. A column called `risk_ids` here would be
  followed by `estimate_ids`, which is where one run table starts growing one column per
  generator.

- **The subject list is resolved in the request, not in the worker.** The register moves
  while a queue drains, and a pass whose subjects were computed at execution time would
  silently cover a different set from the one the analyst was looking at.

- **`agents/_parsing.py` extracted rather than duplicated.** The `bool`-is-an-`int`
  confidence guard is the concrete argument: subtle enough that a second copy would not
  have it, and `True` becoming a confidence of 1.0 makes the most confident row in the
  inbox the one the model was least sure about.

- **`generation_execute.py` dispatches by `kind`.** `generation_dispatch` and
  `tasks/generation.py` both held a hard import of `risk_generate`, which worked for as long
  as there was one generator. An unknown kind fails the run naming the kind rather than
  falling back to identification and recording the wrong pass as a success.

## Shape

```
app/agents/_parsing.py            shared, pure — decode, text, confidence, refs
app/agents/qual_eval.py           pure — scale rendering, prompt, response admission
app/agents/types.py               + Scale, Level, ImpactArea, RiskSubject,
                                    Assessment, EvidenceItem, Skip, five reasons
app/services/qual_generate.py     orchestration; only write is propose()
app/services/generation_execute.py  kind -> executor
api/routes/generation.py          + POST /generation/qualitative-evaluation, ?kind=
alembic/versions/0024_...py       subject_ids, skipped
```

## Verified

Full backend suite in a fresh clone with the delivery unpacked over it: **1316 passed, 3
skipped** (baseline on `main` was 1239). Migration `0024` executed against SQLite and
rendered offline for Postgres, upgrade and downgrade. `ruff check` clean on every file in
this delivery; the three pre-existing F401s on `main` are untouched.

## Open

- **UI. This is now the fourth API-only P5 delivery.** 5.4's plan deferred the inbox on the
  argument that there would then be real generated rows to design against. That argument is
  spent: there are now creation proposals *and* update proposals, with confidence, evidence
  that resolves, and rationales carrying declared approximations. The next delivery should
  be the corpus view and the proposal inbox, not a fifth generator.
- **Cancel** — still the highest-value backend follow-up, and now worth more: a
  forty-subject pass is forty paid calls. Unchanged from 5.4's note.
- **The staleness guard does not cover a first score.** `observed_value` is NULL for an
  unscored field, and `proposal_ledger._assert_fresh` treats NULL as "makes no claim" and
  lets the accept through. So a person scoring a risk between generation and acceptance is
  silently overwritten. Fixing it means distinguishing "was NULL" from "made no claim" in
  5.1, which is a ledger change and not this delivery's.
- **`confidence` is the model's own, unvalidated.** Nothing yet checks whether a 0.8 means
  anything. The edit deltas the ledger records are the raw material for calibrating it —
  that is S11's job and it needs disposition volume first.
- **No cost ceiling in currency**, only `generation_max_subjects` and
  `generation_max_windows`. Token counts are recorded per run, so the data to build one is
  there.
- **Retrieval is BM25 over a per-search IDF.** A one-chunk corpus cannot produce a hit at
  all — a term in every candidate scores zero by design — which is correct behaviour and
  surprising the first time. Embeddings are still unchosen; nothing here needs them, and
  the quality of what this generator is shown is the first thing that would improve if they
  arrived.
