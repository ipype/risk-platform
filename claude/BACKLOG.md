# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Gantt component choice (Bryntum vs DHTMLX vs Syncfusion). Licence cost and React API
  quality differ materially. Blocks all frontend schedule work.
- Embedding provider: Voyage (hosted, per-token) vs self-hosted BGE-M3 (GPU, or slow on CPU).
  Blocks the ingestion pipeline's index build.
- Single-tenant vs multi-tenant data model. Cheap now, expensive to retrofit after the
  register schema lands.
- Deployment target (cloud, VPC, on-prem). MPXJ's JRE dependency constrains this.

## Subsystems not yet designed in depth

- Monte Carlo engine: LHS, Beta-PERT fitting, JCL scatter, criticality index, SSI.
- Mitigation planning with re-simulation ROI (mitigated vs unmitigated delta).
- Living risk register and the realized-outcome learning loop.
- Report export: template engine, section registry, xlsx/pptx/pdf targets.
- Workshop facilitation mode: Delphi anonymous voting, convergence detection, quorum.

## Watch items

- MPXJ `.mpp` support varies by MS Project version — validate against 2016, 2019, and 365
  files before promising format coverage.
