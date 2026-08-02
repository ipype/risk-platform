repo: ipype/risk-platform
branch: main
path: frontend/src

## Last sync

date: 2026-08-02T15:44:10Z

### Updated in this project

- Sidebar rebuilt as a parent/child scope tree (Portfolio → Program → Project), mirroring `ScopeTree.tsx`.
- Adopted the repo's scope vocabulary: `Pf`/`Pg`/`Pj` kind chips, code suffix, risk-count pill, default-project star.
- Caret twist and 13px-per-level indent follow the real component's collapse behaviour.
- Top-bar breadcrumb now renders the root-to-node scope path, as `scopePath()` does.

## Screen map

| Project screen | Repo files |
| --- | --- |
| Sidebar scope tree | frontend/src/components/scope/ScopeTree.tsx, frontend/src/scope-types.ts, frontend/src/scope.css |
| Top-bar breadcrumb | frontend/src/scope-types.ts (`scopePath`) |
| Portfolio / Program dashboards | not yet grounded in repo code — built from written brief |
| Risk register | not yet grounded in repo code — built from written brief |
| Risk record & quantification | not yet grounded in repo code — built from written brief |
| Workflow, Marketing site | design proposal, no repo source |
