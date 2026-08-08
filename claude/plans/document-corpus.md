# Plan — P5 5.2 Document Corpus + Extraction

Status: **built and delivered 2026-08-08.** Migration `0022`. Backend only.

Runtime-pipeline anchor: the `doc` leg of substrate **E** (evidence service). 5.2 builds
the corpus and the extractors; 5.3 builds retrieval over them.

## Why extraction before the evidence service

Reversed from the order first proposed. The evidence interface should be designed against
real chunks with real locators, not imagined ones — the shape of a PDF bbox and a
spreadsheet cell range decides what a retrieval result can render, and guessing that shape
first would mean rewriting the interface once the extractors existed.

## The one thing this must do well: produce citable chunks

The proposal ledger requires at least one evidence reference per suggestion, and a
reference that cannot be rendered as a single highlight in the source is not evidence — it
is a citation-shaped string. That constraint decides the whole design:

- **A chunk never spans a locator boundary.** Not a page, not a sheet, not a table.
  Chunking purely to a target character count would give better retrieval and worse review,
  and review is what this subsystem exists to make possible.
- **Locators are not normalised.** `{page, bbox}` for PDF, `{paragraph}` / `{table, row}`
  for Word, `{sheet, cells}` for a workbook, `{line_start, line_end}` for text. A
  lowest-common-denominator locator could render none of those highlights.
- **Tables become rows, not prose.** A row carries its own headers, so
  `Consent: Env permit | Days: 90` reads standalone in a result and cites back to one row.

## Decisions locked

- **`app/ingest/` is pure** — bytes in, chunks out; no DB, network, clock or logging. Same
  boundary `app/sim/` holds, for the same reason: an extractor that can be handed a byte
  string and compared against an expected list is one that can be tested at all. Policy
  (dedup, re-extraction, storage) lives in `services/document_ingest.py`.
- **PDF: tables first, then prose with table regions excluded.** The reverse order emits
  every cell twice, and the shredded copy retrieves better than the good one because it is
  longer. Line-based table detection (pdfplumber's default) — a text-alignment strategy
  finds tables in ordinary paragraphs, and a false positive costs more than a miss.
- **A PDF with no text layer is refused, not ingested.** A scan yields an empty string
  rather than an error, and a document with zero chunks looks successful in the list,
  retrieves nothing forever, and gives nobody a reason to suspect the file. OCR is a
  deliberate non-goal.
- **Word is read in body order**, not `paragraphs` then `tables` — the latter puts every
  table at the end of the document regardless of where it sat. Heading paths are tracked as
  a stack and stamped on every chunk beneath.
- **Workbooks open `data_only=True`** — the opposite of the convention for the build
  schedule workbook, and deliberately: `=SUM(D4:D19)` cited under a cost risk tells a
  reviewer nothing and matches no query. The trade is that a file whose formulas were never
  calculated reads as empty, which produces a warning rather than silence.
- **Header row is guessed (first row with ≥2 non-empty cells) and the guess is declared.**
  Estimating workbooks open with title blocks and blank rows; taking row 1 blindly labels
  every column with a fragment of a title.
- **Documents are withdrawn, never deleted.** A chunk id can sit in a proposal's
  `evidence_refs` with no FK behind it. Withdrawal removes a document from retrieval and
  leaves every row in place, so a citation made months ago still opens. Same posture as
  runs and proposals.
- **Dedup by sha256, unique per scope.** Two copies of one source double its weight in any
  retrieval over the text. Scoped rather than global because two projects legitimately hold
  the same standard and each needs citations resolving in its own scope.
- **Chunks carry no `scope_id`.** Scope is the document's; retrieval joins. Denormalising
  would create two places for one fact to be wrong.
- **No web fetcher.** A paste path covers the real case (a permit condition, a spec clause)
  at a fraction of the cost, with no robots policy, auth walls, or JS-rendering question.
  `source_url` is recorded as typed, in the filename rather than a column of its own, so
  nothing implies the platform fetched and verified it.

## Correction to an earlier statement

The vector column is **not** carried from day one. `pgvector.sqlalchemy.Vector` does not
compile against the SQLite the whole suite runs on, so declaring it now would break the
test engine in exchange for storage nothing writes to. Adding it later is one nullable
`ALTER TABLE ADD COLUMN` plus a backfill over rows that already exist. The embedding
provider decision stays off the critical path either way — that part was right.

## Schema (migration 0022)

`document` — `scope_id` (FK RESTRICT), `filename`, `suffix`, `source_kind`, `sha256`,
`byte_size`, `page_count`, `chunk_count`, `warnings`, `title`, `status`,
`withdrawn_reason`, `uploaded_by`, `created_at`.
Unique `(scope_id, sha256)`.

`document_chunk` — `document_id` (FK CASCADE), `ordinal`, `kind`, `text`, `locator`,
`section`, `char_count`. Unique `(document_id, ordinal)`.

## API

`GET /documents/formats`, `GET /documents` (scoped, rolls up), `GET /documents/{id}`,
`GET /documents/{id}/chunks`, `POST /documents` (multipart), `POST /documents/paste`,
`POST /documents/{id}/withdraw`, `POST /documents/{id}/restore`,
`GET /documents/corpus/summary`. No DELETE.

## Dependencies added

`pdfplumber==0.11.10`, `python-docx==1.2.0`. `openpyxl` was already pinned. No JRE, no
system libraries — unlike the parked `.mpp` work.

## Deliberately out of scope

- Retrieval of any kind. 5.3.
- OCR.
- A web fetcher.
- Corpus UI — lands with the first generator, same reasoning as the proposal inbox.
- `.pptx`, `.doc`, `.xls` (legacy binary formats need a different reader each).

## Open items

- Re-extraction invalidates citations made against old ordinals. `services/document_ingest.
  reextract()` exists and is tested but is not exposed through the API, precisely because
  what should happen to the affected `evidence_refs` is undecided.
- PDF table detection is line-based only; a borderless table is extracted as prose.
- No Postgres execution of 0022 — offline render only.
- Chunk size (`TARGET_CHARS = 1000`) is untuned. There is no retrieval to tune against yet.
