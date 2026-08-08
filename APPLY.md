# 5.4 — First AI suggestion generator (risk identification)

Folder-swap. Unpack over the repo root, paths intact. **Delete this file before committing.**

## Commit message

```
feat: risk identification generator, LLM provider seam, creation proposals

Adds the first generator in P5: a sweep over a project's document corpus that
drafts cause-event-effect risk statements and raises each as a creation proposal
in the ledger. Nothing generated reaches the register without a human
disposition.

- app/llm/: provider seam (types, fake, anthropic over httpx, registry).
  LLM_PROVIDER and LLM_MODEL both default to empty and refuse.
- app/agents/: pure. Prompt construction, grounded parsing, deduplication.
  No DB, no network, no clock — same boundary as app/sim/ and app/ingest/.
- services/risk_generate.py: orchestration. Corpus -> windows -> call -> parse
  -> dedupe -> proposal_ledger.propose.
- proposal_apply.py: creation registry and the risk creator, materialising the
  creation proposals 5.1 deliberately left unapplied.
- generation_run table and two proposal columns (migration 0023).
- POST /generation/risk-identification, GET /generation/runs[/{id}[/proposals]].

150 new tests. 1089 -> 1239 passing.
```

## Files

New (22):
```
backend/app/llm/{__init__,types,fake,anthropic,registry}.py
backend/app/agents/{__init__,types,risk_id,dedupe}.py
backend/app/models/generation.py
backend/app/services/{risk_generate,generation_dispatch}.py
backend/app/tasks/generation.py
backend/app/api/routes/generation.py
backend/alembic/versions/0023_generation_run.py
backend/tests/{test_llm_providers,test_risk_id_agent,test_agent_dedupe,
               test_risk_generate,test_proposal_creation_apply,
               test_generation_api,test_generation_migration}.py
claude/plans/risk-identification.md
```

Modified (10):
```
backend/app/models/proposal.py        created_target_id, generation_run_id
backend/app/services/proposal_apply.py  creation registry + risk creator
backend/app/services/proposal_ledger.py generation_run_id passthrough
backend/app/core/config.py            llm_* and generation_* settings
backend/app/core/errors.py            LlmError family, GenerationNotRunnable
backend/app/api/errors.py             503 / 502 / 422 handlers
backend/app/db/base.py                registers GenerationRun
backend/app/main.py                   mounts the generation router
backend/app/worker.py                 includes app.tasks.generation
backend/requirements.txt              httpx==0.28.1
```

No frontend files change. `tsc --noEmit` and `vite build` were not run for that
reason; nothing in this delivery can affect them.

## Before you commit — the root `APPLY.md` again

`APPLY.md` is **tracked at the repo root on `main`**: 5.3's copy got committed, the same
way one did before. This zip's `APPLY.md` overwrites it, so after you unpack, deleting the
file shows up as `D APPLY.md` rather than as an untracked file disappearing.

That deletion is the correct cleanup. `git rm APPLY.md` and let it go out with this commit,
or the same thing happens again next delivery. Nothing in the repo reads it.

Also new in this zip: `claude/plans/risk-identification.md`, per the one-file-per-initiative
convention. That one *is* meant to be committed.

## Verify

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt --break-system-packages
python -m pytest -q                  # expect 1239 passed, 3 skipped
python -m ruff check app/ tests/     # expect 3 pre-existing F401s, no new ones
```

Migration 0023 is verified two ways in `tests/test_generation_migration.py`:
executed against a hand-built pre-0023 SQLite database, and rendered offline for
Postgres. `alembic upgrade head` against SQLite still does not work and never
has (0001's unconditional `CREATE EXTENSION`).

`alembic upgrade head` against the real Postgres before starting the API.

## Configuration

Nothing generates until these are set. Add to `.env`:

```
LLM_PROVIDER=anthropic
LLM_MODEL=<model string>
ANTHROPIC_API_KEY=<key>
```

`LLM_PROVIDER=fake` gives a deterministic offline provider for a demo install.
Both settings default to empty and refuse: a fake reached by accident fills a
reviewer's inbox with invented proposals that look exactly like real ones, and a
live provider reached by accident spends money on a misconfiguration.

Optional, with the defaults shown:
`LLM_TEMPERATURE=0.0`, `LLM_MAX_OUTPUT_TOKENS=4096`, `LLM_TIMEOUT_SECONDS=120`,
`GENERATION_MAX_WINDOWS=20`, `GENERATION_WINDOW_CHARS=12000`,
`GENERATION_TRANSCRIPT_CHARS=20000`, `GENERATION_EAGER=false`,
`GENERATION_REQUIRE_WORKER=true`.

The worker must be running (`docker compose up -d worker`) unless
`GENERATION_EAGER=true`. Dispatch refuses rather than queueing into an empty
cluster.

## Decisions made without being asked — all revertible

1. **Identification is a corpus sweep, not a retrieval query.** You cannot BM25
   for risks nobody has thought of yet, so the corpus is walked window by window
   and every chunk is read once. `services/evidence.py`'s `search()` is therefore
   not called by 5.4 — it stays the right interface for the query-shaped
   generators (a probability suggestion asks about a *named* risk). Evidence refs
   are built in `Evidence.as_ref()` shape and the `kind` strings are imported
   from `evidence.py` so a stored ref that stops resolving is a broken import
   rather than a silent mismatch. **This is the call most likely to draw an
   objection.** Reverting means routing identification through `search()`, which
   would need a query-generation step this does not have.

2. **Deduplication lives in the generator, with two thresholds.** ≥0.75 token
   overlap against the register suppresses and reports; 0.45–0.75 keeps the
   candidate and attaches the existing risk as a second citation. Asymmetric on
   purpose: a false suppression is invisible and permanent, a false pass costs a
   reviewer four seconds. Without this a second pass over the same corpus doubles
   the inbox — creation proposals are exempt from the one-pending-per-field index,
   so nothing in 5.1 prevents it. Thresholds are two constants in
   `agents/dedupe.py`.

3. **`created_target_id` is a new column, not a back-fill of `target_id`.**
   Back-filling would move accepted creations into the partial unique index's
   scope and destroy the only signal saying a proposal made a row rather than
   changed one.

4. **Neither new proposal column takes a foreign key.** SQLite cannot add a
   constraint to an existing table, so an FK would mean `batch_alter_table`
   rebuilding `proposal` — dropping and re-declaring the partial unique index and
   both CHECKs, which are the three things on that table it would be worst to get
   subtly wrong. Generation runs are never deleted, so the integrity is already a
   property of the other table.

5. **Raw `httpx`, not the vendor SDK.** One call shape, four wire fields. The
   deciding argument is the run transcript: a thin client makes it literally the
   request and the response.

6. **No retries in the provider.** A generation run is already queued,
   append-only, and carries a status and an error field. A retry over a
   non-idempotent paid call is an operator decision, not a default.

7. **Creation payloads carry no probability, impact or status.** Identification
   says what the risk is; the numbers come from an elicitation with the people who
   own the work. Shipping a probability inside a creation payload would get it
   accepted as a side effect of accepting the risk statement — one click, two
   decisions, one of them invisible. Whitelist is `CREATABLE_RISK_FIELDS`.

8. **`generation_run.status` has four values, not five.** `cancelled` belongs to
   a cancel feature that is not in this delivery; a value nothing can set is dead
   surface. Same reason `simulation_run` took its cancel status in 0018.

## Known gaps

- **No cancel.** A twenty-window pass is twenty paid calls and there is no way to
  stop one halfway. Needs a fifth status, a revoke path, and a decision about a
  worker mid-call. Highest-value follow-up.
- **No frontend.** 5.1–5.4 are all API-only. The corpus view and proposal inbox
  ("5.3b") are still unbuilt, and there are now real generated rows to design the
  inbox against, which was the argument for deferring it.
- **Runs are auditable, not replayable.** No temperature makes a model
  deterministic across time and deployments. The run stores prompt version,
  provider, model, temperature, a `pack_sha256` fingerprint of the extracts sent,
  and the raw responses — enough to see what was asked and answered, and
  deliberately not called a seed.
- **`candidate_count` undercounts an unparseable window.** A response that yielded
  no JSON contributes zero candidates because there is nothing to count; the drop
  is recorded with reason `unparseable`.
- **No cost ceiling in currency**, only `GENERATION_MAX_WINDOWS`. Token counts are
  recorded per run.
- **Frontend test runner still absent.** Unchanged standing gap.

## Try it

```bash
curl -X POST 'localhost:8000/documents/paste?scope_id=1' \
  -H 'content-type: application/json' \
  -d '{"filename":"consent.txt","text":"The environmental consent is valid for ninety days from issue. Dewatering may not begin before the consent has been granted."}'

curl -X POST 'localhost:8000/generation/risk-identification?scope_id=1' \
  -H 'content-type: application/json' -H 'X-Actor: Sam' -d '{}'

curl 'localhost:8000/generation/runs/1'
curl 'localhost:8000/generation/runs/1/proposals'
curl -X POST 'localhost:8000/proposals/1/disposition' \
  -H 'content-type: application/json' -H 'X-Actor: Sam' -d '{"action":"accept"}'
curl 'localhost:8000/risks?scope_id=1'
```
