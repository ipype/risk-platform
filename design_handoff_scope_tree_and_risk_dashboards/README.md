# Handoff: iPype scope tree + scoped risk dashboards & register

## Overview

This bundle specifies four things to build in `ipype/risk-platform`:

1. A **parent/child scope tree sidebar** (Portfolio → Program → Project) that replaces flat level navigation.
2. **Scope-derived dashboards** — every figure on a Portfolio or Program dashboard is computed from the selected node and its subtree, never hardcoded.
3. A **dense risk register** scoped to the selected node and its descendants.
4. A **risk record** screen carrying the full lifecycle (Identify → Assess → Quantify → Respond → Decide → Monitor) with quantification output and audit trail.

The design deliberately builds on what already exists in the repo: `frontend/src/components/scope/ScopeTree.tsx`, `frontend/src/scope-types.ts`, `frontend/src/scope-state.ts`, `frontend/src/ScopeContext.tsx` and `frontend/src/scope.css`. Vocabulary (`ScopeKind`, `buildScopeTree`, `scopePath`, `subtreeIds`, `Pf`/`Pg`/`Pj` chips, `risk_count`, `is_default`) is taken from those files — do not invent parallel concepts.

## About the design files

`iPype Risk Platform.dc.html` in this folder is a **design reference created in HTML**. It is a prototype showing intended look and behaviour — it is **not production code to copy**. The task is to recreate these designs in the existing frontend environment (React 18 + TypeScript + Vite, plain CSS in `frontend/src/*.css`) using established patterns there. Extend `scope.css` and the existing `ScopeTree` component rather than introducing a new styling approach or component library.

Open the file in a browser. The left rail switches between all six screens.

## Fidelity

**High fidelity.** Colours, type, spacing and density are final and specified below. Recreate faithfully, but map to the repo's CSS variables (`--card`, `--border`, `--muted`, `--text`, `--primary`) where an equivalent exists rather than hardcoding hexes a second time. Where the prototype's neutral palette differs from the current app palette, the app palette wins — the density, hierarchy and information design are what this handoff is asserting.

---

## Screens

### 1. Scope tree sidebar (all screens)

**Purpose:** navigate the hierarchy and set the app-wide scope. Selecting a node is the single act that re-scopes every view.

**Layout**
- Fixed rail, `width: 268px` (matches the existing `.scopeside { width: 268px }` — do not narrow it; at 212px every scope name truncates).
- `position: sticky; top: 0; height: 100vh; overflow-y: auto` on the rail itself. **The rail is the only scroller.** Do not give the tree its own inner scroll container: with a header, tree, secondary nav, callout card and user footer as siblings, an inner scroller with `min-height: 0` absorbs the entire flex deficit and collapses to a fraction of its content height. All rail children are natural height.
- Vertical order: brand header (48px content, 1px bottom border) → "SCOPES" label row with a `+` affordance → tree → "WORK" label → secondary nav → flexible spacer → optional Phase 2 callout → user footer.

**Tree row anatomy** (mirrors `ScopeTree.tsx` exactly)
- Row: `display: flex; align-items: center; gap: 5px; min-height: 27px; border-radius: 4px; cursor: pointer`.
- Indent: `padding-left: 6 + depth * 13` px. (The current component uses `6 + depth * 14`; either is fine — be consistent.)
- **Twist**: 13px wide, centred, `▾` when open / `▸` when closed, empty span when leaf. `stopPropagation` on its click so expanding never re-scopes. Keep `tabIndex={-1}` as the existing component does.
- **Kind chip**: `Pf` / `Pg` / `Pj`, 9px 600 mono, `padding: 2px 4px`, `border-radius: 3px`. On the dark rail: portfolio `#1f3c58` on `#8fbde8`; program `#463714` on `#dcbc70`; project `#1c3a26` on `#8ccfa4`. On a light rail keep the existing `.scopechip.kind-*` colours.
- **Name**: `flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap`, 12px, weight 600 when selected else 400.
- **Default star**: `★`, 9px, `#a9761a`, only when `is_default`.
- **Code**: 9.5px mono, muted — **rendered only on the selected row**. This is deliberate: codes cost ~30px per row and are the difference between "Southern Loop 1/2/3" being distinguishable and all three reading "South…".
- **Count pill**: 9.5px 500 mono, `border-radius: 999px; padding: 1px 6px`, muted background.

**Counts — API change required**
The tree shows **subtree totals**, so "Northline Expansion" reads 164 rather than 0. `ScopeNode.risk_count` is documented as direct rows only ("Zero on programs and portfolios by construction"). Add a rolled-up count server side — `risk_count_subtree` on the `GET /scopes` payload — rather than computing it client side by walking every node on every render. The prototype computes it client side only because it has no server.

**Keyboard:** keep the existing roving-tabstop behaviour verbatim (ArrowUp/Down move focus, ArrowRight/Left open/close then move, Home/End, Enter/Space select, one tab stop for the whole tree). No change is being asked for there.

**Selection routing**
| Selected kind | Screen shown |
| --- | --- |
| portfolio | Portfolio dashboard |
| program | Program dashboard |
| project | Risk register, scoped to that project |

**Breadcrumb:** the top bar shows the root-to-node path joined with ` / `, from `scopePath()`. 10.5px mono, `#8c9096`.

---

### 2. Portfolio / Program dashboard

**Purpose:** answer "are we inside appetite, and what is driving us out of it".

**Layout:** `padding: 18px 22px 40px`. Top to bottom:
1. **Scope chip row** — 4 chips, 11px mono, `#fff` on `1px solid #e2e2df`, `border-radius: 3px`, `padding: 4px 9px`.
   - Portfolio: `Portfolio: {name}` · `{n} programs · {m} projects` · `Appetite {money}` · `Board reporting: monthly`
   - Program: `Program: {name}` · `{n} projects` · `Owner: {owner}` · `Tolerance {money}`
2. **KPI strip** — 5 equal columns, `display: grid; gap: 1px` on a `#e2e2df` background inside a 1px border with `border-radius: 5px; overflow: hidden` (the gap-as-hairline technique). Each cell `padding: 12px 14px 13px`: 9.5px mono uppercase label (`letter-spacing: .07em`, `#8c9096`) → value 23px/1 600 `letter-spacing: -.6px` → delta 11px mono in red or green → 11px sub in `#75797f`.
3. **Chart row** — two layouts, both valid, pick one (the prototype exposes both as a toggle so you can compare):
   - *trend-led*: exposure trend `minmax(0,1.55fr)` + residual matrix `minmax(0,1fr)`
   - *matrix-led*: residual matrix `minmax(0,1fr)` + tolerance-utilisation bars `minmax(0,1.35fr)`
4. **Bottom row** — `grid-template-columns: minmax(0,1fr) 302px`: top exposures table + a column holding threshold breaches and lifecycle throughput.

> Always write flexible grid tracks as `minmax(0, Nfr)`. A bare `1fr` will not shrink below its content and pushes fixed-pixel table columns out of the card.

**Every figure is scope-derived.** Do not branch on "is this a program" — key on the selected node id:
- Open risks = subtree rollup for the node. It must equal the sidebar pill for the same node; two different numbers for one node on one screen is the specific bug to avoid.
- Exposure P80, tolerance/appetite, owner, quantified %, response coverage, overdue actions = per-scope values from the API.
- Sub-caption reads `Tolerance $X — breached` when exposure ≥ tolerance, `— within limit` otherwise. Delta colour follows the same test.
- Tolerance-utilisation bars list the **children of the selected node** (programs under a portfolio, projects under a program).
- Threshold breaches are filtered to `subtreeIds(selectedId)`. A portfolio-level appetite alert therefore never appears on a program dashboard. Empty state: one green row, "No threshold breaches in this scope".
- Lifecycle throughput bars and the "N risks have sat un-quantified for >30 days" caption scale off the same rollup.

**Residual risk matrix:** 5×5 CSS grid, `gap: 3px`, cells `aspect-ratio: 1.35`, `border-radius: 2px`, count centred in 12px mono. Probability descends top to bottom, impact ascends left to right. Band colours by `p × i`: ≥15 `#c9534a` on white; ≥10 `#dd9a4e` on `#3a2a0e`; ≥5 `#e8dfae` on `#4a4322`; else `#dbe6d9` on `#3c4b3a`. Axis labels 9.5px mono `#9a9ea4`; "PROBABILITY" runs vertically (`writing-mode: vertical-rl; transform: rotate(180deg)`). Cells are clickable — hover `outline: 1.5px solid #17181a` — and should filter the register to that band.

**Exposure trend:** `viewBox="0 0 620 190"`, `preserveAspectRatio="none"`. Horizontal gridlines `#f0f0ee`; P80 line `#1f5c8b` 1.8px over a `#eaf0f5` fill; P50 `#a8b6c2` 1.4px dashed `4 3`; appetite `#b23b30` 1px dashed `2 3`. Legend row below, separated by a `#f0f0ee` hairline. **In the prototype this curve is illustrative and labelled as such** — wire it to real series data before removing that label.

---

### 3. Risk register

**Purpose:** the working surface. Scan, filter, open.

**Layout**
- Sticky filter bar under the top bar (`top: 50px`), `#fff`, 1px bottom border, `padding: 10px 22px`, chips at `gap: 7px`. First chip is the scope filter: `Scope: {name} + below`, active styling (`#eaf0f5` on `1px solid #b9cddc`, text `#1f5c8b`). Right side: `{open} open · {closed} closed · showing {n}` in 10.5px mono — all three from the current scope, not global totals.
- Table card: `#fff`, `1px solid #e2e2df`, `border-radius: 5px`, **`overflow-x: auto`**.
- Columns, as an 11-track grid repeated on the header and every row:
  `70px minmax(210px,1fr) 96px 92px 34px 34px 52px 96px 90px 92px 80px`, with `min-width: 950px` on both so the card scrolls rather than clipping.

> The register must **scroll** horizontally. `overflow: hidden` on a container narrower than the track sum silently deletes the right-hand columns with no way to reach them.

- Header: `#fafafa`, 9.5px mono uppercase `#8c9096`, `letter-spacing: .06em`, `padding: 8px 6px 8px 0`.
- Row: `border-bottom: 1px solid #f4f4f2`, hover `#fafbfc`, cell vertical padding **7px compact / 11px standard** (one switch, applied to every cell).
- Columns: ID (11px mono, `#1f5c8b`) · Risk (12.5px title, ellipsised, with 10px mono category beneath) · Owner · Level · P · I · Score · Cost P80 · Sched. · Status · Review.
- Numerals are mono and right-aligned. P/I/Score centred.
- **Score chip**: `min-width: 24px; padding: 1px 5px; border-radius: 2px`, 11px 500 mono. ≥16 `#b23b30` on `#f7e7e4`; ≥9 `#a9761a` on `#f9f0e0`; ≥5 `#5b5f66` on `#f0f0ee`; else `#2e7248` on `#e6f0e8`.
- **Status pill**: 10.5px mono, `padding: 2px 6px`. Identified `#5b5f66`/`#f0f0ee`; Assessed `#1f5c8b`/`#eaf0f5`; Quantified `#2e7248`/`#e6f0e8`; In response `#a9761a`/`#f9f0e0`; Monitoring `#75797f`/`#f0f0ee`.
- **Review** turns `#b23b30` when overdue.
- Footer: `Rows 1–{n} of {total}` + Prev/Next.

**Scoping:** rows are those whose owning scope is in `subtreeIds(selectedId)`. Per `scope-state.ts` the server already answers for the node and everything under it — so this is a request parameter, not a client filter. Risks owned at an ancestor level do not appear when a descendant is selected.

---

### 4. Risk record

**Purpose:** one risk, whole lifecycle, defensible numbers.

**Header block** (`#fff`, 1px bottom border, `padding: 15px 22px 0`)
- ID 11px mono `#1f5c8b` · severity pill (`Critical · residual 20`, red on `#f7e7e4`) · type (`Threat` / `Opportunity`) 10.5px mono.
- Title 19px/1.3 600, `letter-spacing: -.3px`, `text-wrap: pretty`, max-width 640px.
- Cause/effect paragraph 12.5px/1.6 `#5b5f66`. **Enforce cause-and-effect phrasing in the create form** — a register full of one-word titles cannot be quantified.
- Actions right-aligned: secondary `Escalate`, primary `Approve response`.
- **Lifecycle stepper**: 6 equal segments, `border-bottom: 2px` — current stage `#1f5c8b` with `#f7fafc` fill and blue label; complete `#dcdcd9`; not started `#efefec` with `#9a9ea4` label. Each shows `01`…`06`, name, and a state line (`Complete · 19 Mar`, `Current · P80 $2.4M`, `2 of 4 actions live`, `Awaiting sponsor`).

**Body:** `grid-template-columns: minmax(0,1.5fr) minmax(0,1fr)`, `gap: 14px`.

Left column:
- **Cost impact distribution** — 24-bar histogram, `display: flex; align-items: flex-end; gap: 2px; height: 132px`, bars `flex: 1`, `border-radius: 1px 1px 0 0`. Body of the distribution `#1f5c8b`, upper-middle `#8fa9be`, tail beyond P80 `#c9534a`. Axis hairline + 9.5px mono ticks. `Converged` badge, green, in the header — surface non-convergence loudly.
- **Percentile strip** — P10/P50/P80/P90 in a four-up hairline grid, `#fafafa` cells, value 16px 600; P80/P90 in red.
- **Response actions** — checkbox (14px, 1.5px border, filled green with `✓` when complete), text 12.5px/1.45, then `{owner} · due {date} · {cost}` in 10px mono, status pill right. `+ Add action` row at the bottom in `#1f5c8b`.

Right column:
- **Assessment** key/value list — label `#75797f` 11.5px sans, value 11.5px mono right-aligned: probability, cost impact, schedule impact, inherent score, residual score, strategy, category, next review.
- **Risk Copilot** (Phase 2, hideable) — warm card `#fbfaf7` on `#e6e2d8`, amber dot, `Phase 2` badge. Body is a *challenge*, not an assertion: compares this estimate against comparable closed risks and states the consequence of accepting it. Two buttons: `Apply suggestion`, `Dismiss`. **It must never write a score without an explicit accept step, and every accepted suggestion lands in the audit trail attributed to the Copilot.**
- **Audit trail** — 1px `#e8e8e5` rail with 6px dots, event text 11.5px/1.45, `{actor} · {timestamp}` in 10px mono.

---

### 5. Workflow page

A reference page, not app UI: the six lifecycle stages with owner and **gate** for each, what each level sees, and the design principles this handoff is built on. Worth porting as internal documentation because the gates are the product rule:

> **A risk above score 9 cannot roll up past the quantification gate.** That single constraint is what makes the portfolio number defensible, and it belongs in the API, not in guidance text.

### 6. Marketing site

Separate deployment. Note only that it must **not** render app chrome — no reporting-period label, no Export/New risk buttons, no risk counts. A public page under logged-in furniture reads as a bug.

---

## Interactions & behaviour

- **Select node** → sets app scope, routes to the screen for its kind, refetches. Click, Enter or Space. Explicit and expensive, as documented in `ScopeTree.tsx`.
- **Twist** → local expand/collapse only. `stopPropagation`. Collapse state is view state, not app state.
- **Row click** (register or top exposures) → risk record.
- **Matrix cell click** → register filtered to that probability/impact band.
- **`Open full register →`** on the dashboard → register at the current scope.
- Hover: table rows `#fafbfc`, tree rows a subtle lift, buttons one step darker (primary `#1f5c8b` → `#18496e`).
- No transitions specified anywhere. This is a dense data tool; motion on a 300-row table is noise.

## State

| State | Scope | Notes |
| --- | --- | --- |
| `scopeId` | app, persisted | Already handled by `ScopeContext` / `scope-state.ts` / `resolveSelection`. |
| `collapsed: Set<number>` | sidebar, local | Existing behaviour. |
| `focusId` | sidebar, local | Roving tab stop. Existing. |
| `screen` / route | app | Derived from selected kind on selection; user can navigate away without changing scope. |
| register filters | per screen | Scope chip is derived from `scopeId`, not independently editable. |
| `density` | user preference, persisted | compact (7px) / standard (11px) row padding. |
| `showCopilot` | feature flag | Off until Phase 2 ships. |

Data needs: `GET /scopes` (+ subtree rollup counts), scoped risk list with pagination, scoped dashboard aggregate (exposure percentiles, tolerance, coverage, overdue, funnel), risk detail with distribution, actions and audit events.

## Design tokens

**Neutrals** — canvas `#f4f4f2` · surface `#ffffff` · surface-alt `#fafafa` · hairline `#f4f4f2` · border `#e2e2df` · border-strong `#cececa` · text `#17181a` · text-2 `#3f434a` · muted `#5b5f66` · muted-2 `#75797f` · faint `#8c9096` · faintest `#9a9ea4`

**Dark rail** — bg `#1b1d20` · border `#2c2f33` · selected `#2f3338` · hover `#26292d` · text `#d8d9d6` · text-dim `#c2c5c9` · label `#6f7379`

**Accent** — `#1f5c8b`, hover `#18496e`, tint `#eaf0f5`, tint-border `#b9cddc`

**Status** — red `#b23b30` / `#f7e7e4` · amber `#a9761a` / `#f9f0e0` · green `#2e7248` / `#e6f0e8`

**Matrix bands** — `#c9534a` · `#dd9a4e` · `#e8dfae` · `#dbe6d9`

**Type** — IBM Plex Sans (400/500/600) for prose and UI; **IBM Plex Mono (400/500) for every number, ID, code, timestamp and uppercase micro-label**. That split is the main reason the tables read as instrument panels rather than spreadsheets. Scale: 9.5 / 10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 15 / 16 / 19 / 23px. Uppercase labels get `letter-spacing: .06–.09em`; display sizes get negative tracking (`-.2` to `-1.2px`).

**Spacing** — 1 / 2 / 3 / 5 / 6 / 7 / 9 / 11 / 14 / 16 / 18 / 22px. Card padding `11px 14px` header, `12–16px 14px` body; page gutter 22px; card gap 14px.

**Radii** — 2px chips/pills · 3px chips · 4px buttons and tree rows · 5px cards · 999px count pills.

**Shadows** — none, except the drawer shadow already in `scope.css`. Hierarchy comes from hairlines.

## Design principles asserted here

1. **Numbers, then colours.** Heat maps navigate; quantified P-values decide. Never a matrix cell without a path to the money behind it.
2. **One record, many views.** Portfolio/program/project are lenses on one row, never separate registers that drift.
3. **Density is a feature.** Compact rows, mono numerals, right-aligned figures. No card grids for tabular data.
4. **Gates, not guidelines.** Enforce stage gates in the API.
5. **Every figure is traceable.** Any number an executive sees is one click from its inputs, owner and edit history.
6. **AI advises, never asserts.** Reviewable drafts with a visible accept step, always logged.

## Assets

None. The marketing page uses striped SVG placeholders where product screenshots go — replace with real captures. Icons in the prototype are text glyphs (`▾ ▸ ★ ⌕ ⋯ +`), same as the existing component; substitute the codebase's icon set.

## Files

- `iPype Risk Platform.dc.html` — the design reference. Left rail switches screens: Portfolio dashboard, Program dashboard, Risk register, Risk record, Workflow, Marketing site.
- `github.md` — repo association and the screen → repo-file map.
- `screenshots/01–06` — Portfolio dashboard, Program dashboard, Risk register, Risk record, Workflow, Marketing site. Captured at a narrow viewport, so some labels wrap that would not wrap at desktop width — treat the HTML file as the authority on layout and the screenshots as orientation.

Repo files this design extends: `frontend/src/components/scope/ScopeTree.tsx` · `frontend/src/scope-types.ts` · `frontend/src/scope-state.ts` · `frontend/src/ScopeContext.tsx` · `frontend/src/scope.css`

## Suggested order

1. Rail width to 268px and rail-level scrolling; codes on the selected row only. (Fixes name truncation and the collapsing-tree bug.)
2. Subtree rollup counts, server side, on `GET /scopes`.
3. Route selection by kind; breadcrumb from `scopePath`.
4. Scope the register — request parameter, horizontal scroll, score/status chips, density switch.
5. Scope-derived dashboard aggregates. Delete every hardcoded figure.
6. Risk record: stepper, distribution, percentiles, actions, audit.
7. Quantification gate enforced in the API.
8. Copilot behind a flag.
