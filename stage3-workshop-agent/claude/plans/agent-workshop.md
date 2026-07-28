# Plan — Stage 3 Workshop Facilitation Agent

Status: designed, not built. Prompt asset lives at
`backend/app/agents/prompts/stage3_workshop_facilitator.md` (verbatim, source of truth).

## Decisions locked (2026-07-27)

- Workshop mode is **synchronous**: live session, per-turn transcript/typed input, agent
  moderates one risk at a time.
- Agent is **facilitator, not advocate**: presents drafts neutrally, asks one question at a
  time, proposes merge/split as questions only.
- Every disposition (`accept` / `reject` / `merge` / `split`) requires an explicit human
  decision. No default, no inference from discussion lean. Unresolved = stays `draft`.
- No probability/impact/P×I talk (Stage 4's job, post-SME-commit) and no mitigation talk
  (Stage 10's job) inside the workshop agent.
- Live edits are normalized back into cause → risk_event → consequence before recording;
  ambiguous edits trigger a clarifying question, never a guess.

## Data model

- `WorkshopSession`: id, project_id, scheduled_at, state (`scheduled → in_progress →
  closed`), attendees (user ids + free-text external names), agenda (ordered draft-risk
  ids).
- `WorkshopDiscussion`: session_id, risk_id, transcript/log (append-only), disposition,
  reject_reason, moderator_notes. One row per risk per session.
- Draft-risk state machine additions:
  - `split` and `merged` are **terminal** states on the parent/children respectively.
  - Split children get new risk ids, `source = workshop_split`, and re-enter the queue.
  - Merge survivors keep queue status; merged-away ids record `merged_into`.
  - Risks raised live get `source = workshop` (vs `agent_pass_1..4` / `premortem`) and
    enter the same accept/reject queue — no skipping to the register.
- Audit: dispositions append to the same attributed-history pattern as RiskHistory —
  who decided, when, agent-suggested vs human-entered.

## I/O contract

Per turn the agent receives: current risk (cause/risk_event/consequence/category/source/
status), the session's full draft list (overlap detection), the live discussion input, and
prior session decisions. It returns one structured disposition object per risk resolved
(schema in the prompt asset). `status: draft` + moderator_note when unresolved.

## Invariant mapping

- Invariant 4 (AI outputs are proposals): agent proposes merge/split, humans dispose.
- Invariant 5 (append-only audit): reject reasons + dispositions logged, never mutated.

## Open items

- Transcript capture mechanism (typed notes v1; audio transcription later).
- Whether `WorkshopDiscussion` transcript is stored as JSONB event list or child rows.
- Async/Delphi mode remains a separate future variant (see BACKLOG Delphi-style item);
  this plan covers the synchronous mode only.
