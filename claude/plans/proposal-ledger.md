# Plan — P5 5.1 Proposal Ledger + Provenance

Status: **built and delivered 2026-08-08.** Migration `0021`. Backend only.

Runtime-pipeline anchor: substrate **P** (proposal ledger) and substrate **A**
(audit/lineage) from `pipelineruntime.pdf`.

## Why this first

Every P5 stage — ingestion suggestions, workshop dispositions, elicitation drafts,
historical suggestions — writes through this table. One table is what turns "a human can
intervene at every step" (invariant 4) into an architectural property instead of
per-feature UI affordances that each have to be remembered and tested. It is also the only
P5 piece with no LLM or embedding dependency, so it is testable to the existing standard
and defers the embedding-provider decision without blocking on it.

Prerequisite confirmed at build: 4.7 (schema) and 4.8 (scope tree sidebar, scoped routing)
both shipped — `ScopeTree.tsx`, `ScopeBar.tsx`, `ScopeContext.tsx`, `scope-state.ts` are in
the tree. The ledger is therefore born scoped rather than retrofitted.

## Decisions locked

- **One polymorphic table**, not per-domain proposal tables. `(target_type, target_id,
  field_path)` addresses anything. Referential integrity to the target becomes a service
  rule rather than an FK; the applier registry is the gatekeeper and fails loudly on a
  type it does not know.
- **Park is a substate of pending** (`parked` flag), not a status. The terminal set stays
  exactly `accepted | edited | rejected | superseded`.
- **Merge is supersession with a pointer.** Merging A into B disposes A as `superseded`
  with `superseded_by = B`. Semantic merging of draft-risk *content* is the workshop
  agent's job.
- **Newest pending wins.** A new proposal for the same `(target_type, target_id,
  field_path)` supersedes the prior pending one automatically, enforced by a partial
  unique index. Creation proposals (`target_id IS NULL`) are exempt.
- **Terminal is immutable.** No transition out of a terminal status, no DELETE route, no
  PATCH of a disposition.
- **Provenance lands on history, not on domain rows.** `risk_history.provenance` is
  nullable; NULL reads as human. A provenance column on `risk` itself would be overwritten
  by the next edit and would answer only for the most recent one. *Revertible.*
- **`generator` as two flat columns**, not a JSON blob — every question of this field is a
  GROUP BY.
- **Reject requires a note.** The reason is half the signal a later ranking pass learns
  from.
- **`observed_value` captured at proposal time**, so an accept that would overwrite a
  newer human edit is refused with both values rather than resolving silently in the
  model's favour. *Revertible.*
- **`risk_level`, `impact`, `status`, `custom_fields` are not proposable.** The first two
  are derived by the applier's scoring pass, `status` is a workflow decision, and
  `custom_fields` is a free-form dict a single `field_path` cannot address.

## Schema — `proposal` (migration 0021)

Columns: `id`, `scope_id` (FK `scope_node.id` RESTRICT), `target_type`, `target_id`
(nullable), `field_path`, `proposed_value`, `observed_value`, `rationale`,
`evidence_refs`, `confidence` (nullable = abstained), `generator_model`,
`generator_prompt_version`, `status`, `parked`, `applied_value`, `superseded_by` (self-FK),
`disposed_by`, `disposed_at`, `disposition_note`, `created_at`.

Constraints:
- `ck_proposal_status` — closed status vocabulary.
- `ck_proposal_has_evidence` — `json_array_length(evidence_refs) >= 1`. In the database,
  not only at the Pydantic boundary, because a generator writing through the service
  bypasses that boundary. Verified under both dialects.
- `uq_proposal_one_pending_per_field` — partial unique on `(target_type, target_id,
  field_path) WHERE status = 'pending' AND target_id IS NOT NULL`.
- Indexes on `scope_id`, `status`, `created_at`, `(target_type, target_id)`.

`risk_history` gains `provenance VARCHAR(160) NULL`. No backfill.

Evidence ref shape: `{kind, ref, excerpt?}`. `kind` stays a free string until 5.2 defines
the source set.

## Status machine

```
pending ──accept──▶ accepted     applied_value = proposed_value
pending ──edit────▶ edited       applied_value ≠ proposed_value; delta is the signal
pending ──reject──▶ rejected     note required
pending ──(system)▶ superseded   newer pending for same target/field, or merge
pending ⇄ park/unpark            flag only; status unchanged
terminal ──▶ ∅                   409
```

Apply runs *before* the status is written, inside the route's transaction. An applier
failure leaves the proposal pending — accepted-but-not-applied is unreachable.

## Application

`services/proposal_apply.py` — registry keyed on `target_type`. One applier ships:
`risk`, writing through the same snapshot/diff/rescore path `PATCH /risks/{id}` uses, so
the audit row is indistinguishable from a human edit except for `provenance`. A test pins
that the two paths produce the same band. Creation proposals record a decision but are not
materialised — that arrives with the draft-risk pipeline.

## API

`GET /proposals` (scoped, rolls up; filters status/target_type/target_id/parked),
`GET /proposals/{id}`, `POST /proposals`, `POST /proposals/{id}/disposition`,
`POST /proposals/{id}/park`, `GET /proposals/inbox/count`. No DELETE, no PUT.

## Deliberately out of scope

- Any generator, any LLM call, the evidence service (5.2).
- Proposal inbox UI — lands with the first real generator so it is designed against real
  rows. *Revertible.*
- Surfacing `provenance` in the history view — every value is NULL until a generator
  exists, so the column would render empty on every row.
- Migrating the existing mapping suggestion engine onto the ledger.
- Provenance on `mapping_history` and the mitigation trail.

## Open items

- S11 export format for accepted/edited/rejected deltas.
- Park/unpark events are unaudited.
- Evidence-ref `kind` vocabulary — pinned by 5.2.
- Postgres regression coverage for the partial index and the RESTRICT self-FK. The offline
  render proves the DDL compiles; nothing has executed it under Postgres.
