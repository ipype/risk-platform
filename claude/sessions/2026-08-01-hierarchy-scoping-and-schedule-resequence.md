# 2026-08-01 — hierarchy/scoping design + schedule sanity check + resequence

No commits this session. GitHub MCP stayed read-only throughout (per standing note); the
only artifact delivered was `Risk_Platform_Build_Schedule.xlsx`, which is Sam's local
planning file, not tracked in the repo — nothing here needed a zip for git apply.

## What happened

**1. Schedule sanity check.** Compared the workbook's % Done cells against `CLAUDE.md`,
`ACTIVE.md`, and `BACKLOG.md`. The workbook was stale in both directions — some tasks
undercounted (2.1 MPXJ bridge, 3.3 Iman-Conover, 3.4 percentile-correct integration, 3.7
schedule risk application were all functionally done but marked 0%), one intentionally
descoped task was marked incomplete rather than parked (2.3 `.mpp`), and P4's tornado/
criticality work existed in the repo under a phase number the workbook never gave it a line
item for. Corrected 12 rows, each with a note citing the source line in ACTIVE/BACKLOG.
Overall completion moved 37% → 48.4% (225/465h at the time).

**2. Portfolio/program/project hierarchy — brainstormed and refined.** Sam's instinct
(collapsible tree sidebar, click a project to scope every page to it, program register
shows a source-project column) was correct as the core mechanic. Refined to:

- Selected tree node is a **scope context** applied to existing pages, not separate program
  pages — cheaper to build and maintain, and it means hierarchy support is inherited by
  every future page for free.
- Program register holds three risk classes: rolled-up (read-only, edited only at project
  level), escalated (project-owned, flagged upward past a threshold), and program-native
  (interfaces, shared procurement, weather at a shared site — mapped to activities across
  *multiple* projects using the existing shared-draw Hulett semantic).
- Two QRA rollup methods, user-selectable per run: **Method A**, an integrated master
  schedule through the existing `.xer` pipeline with the DCMA gate unchanged; **Method B**,
  per-iteration aggregation of already-persisted child `RunArrays`, re-correlated with
  Iman-Conover across projects and combined inside each iteration before percentiling —
  this is invariant #1 (never add percentiles) extended to portfolio scope: **portfolio P80
  ≠ sum of project P80s**.
- Quantified impacts surface in the register per risk from its last accepted run, but only
  as: mean cost/schedule impact (means are safely additive), contribution-to-contingency
  from the tornado decomposition, a rank badge, and a staleness flag if estimates changed
  since that run. Deliberately not a raw per-risk P80 — that would recreate the additive-
  percentile mistake at the register level.
- Two smaller ideas surfaced, not scheduled: a portfolio heat view (small-multiples risk
  matrix, one per project) and cross-project dedup on rollup (name/RBS-code clustering as a
  cheap preview of the P5 dedup work — four projects independently carrying the same risk
  is itself a signal that it's a program risk).
- **Sam confirmed strict tree**: one parent per node, no project shared across programs.
  This also resolves the `BACKLOG.md` Blocked item on single- vs multi-tenant data model —
  the hierarchy schema *is* that decision.

**3. Schedule resequenced.** Hierarchy work was first added as P8 (86h, 8 tasks) after P7.
Sam pushed back: build the schema and scope routing before anything else moves forward.
Pulled the two foundational tasks — hierarchy schema + backfill migration, and the scope
tree sidebar with scoped routing — forward into **P4 as 4.7 and 4.8** (22h), positioned
after the simulation work that already exists and before any P5 table gets created, so the
AI agent's corpus/suggestion/workshop tables are born with a scope foreign key rather than
retrofitted later. P8 shrinks to 64h across the remaining six tasks (register rollup,
shared/escalated risks, quantified-impact surfacing, Method A, Method B, dashboards).
Rebuilt the full formula chain, the nine Gantt conditional-formatting ranges, and the Setup
sheet's phase table references; verified the workbook reopens clean. Total effort:
465h → 551h. MVP milestone redefined as "hierarchy-aware."

## Decisions made
- Scope-as-context over separate program pages.
- Three-class program register model (rolled-up / escalated / program-native).
- Two rollup methods (master schedule vs. per-iteration aggregation), both required —
  neither alone covers program and portfolio scope.
- Register shows mean + contribution + rank + stale flag, never a raw per-risk percentile.
- Strict tree hierarchy — resolves the tenancy Blocked item.
- 4.7/4.8 (schema + scope routing) build before P5, not after P4's analytics work.

## Docs found stale
- The workbook's % Done cells lagged ACTIVE.md/BACKLOG.md in several places (see above) —
  corrected in the delivered file, not in the repo (the workbook isn't tracked in git).

## Surfaced for later
- Portfolio heat view (small-multiples matrix) — not scheduled, no WBS line yet.
- Cross-project dedup on rollup — not scheduled, no WBS line yet.
- 4.7's backfill migration touches existing tables (`register`, `schedules`,
  `simulation_run`, etc.) — needs the offline Alembic SQL check plus a genuinely separate
  `AsyncSession` round-trip test per the standing verification method, given the SQLite
  `ondelete="CASCADE"` gotcha already on file.
