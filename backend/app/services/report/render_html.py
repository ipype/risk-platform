"""One document, one file, printable.

No template engine and no CSS framework, for the same reason the Gantt is hand-rolled:
this produces one page whose entire vocabulary is five block kinds, and a dependency that
has to be installed on every deployment to save forty lines of string building is a bad
trade.

The output is a single self-contained file. It has to survive being emailed, opened
offline, printed to PDF from a browser, and read on a phone — so styles are inline, tables
scroll rather than overflow, and ``@page`` sets the print margins. Band colours are
backgrounds behind text that also names the band, because a monochrome printer and a
colour-blind reader are both ordinary cases rather than edge ones.
"""

from __future__ import annotations

from app.services.report.model import (
    Callout,
    Cell,
    Column,
    Document,
    KeyValues,
    MatrixBlock,
    Paragraph,
    Section,
    Table,
    format_value,
)

__all__ = ["render_html"]

_TONE_LABEL = {"info": "Note", "warning": "Warning", "method": "Method"}

_CSS = """
:root {
  --ink: #111827;
  --muted: #4b5563;
  --line: #d1d5db;
  --head: #1f2937;
  --paper: #ffffff;
  --wash: #f9fafb;
  --warn: #b45309;
  --warn-bg: #fffbeb;
  --method: #1d4ed8;
  --method-bg: #eff6ff;
  --info: #374151;
  --info-bg: #f3f4f6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 20px 64px;
  background: var(--wash);
  color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
        sans-serif;
}
.sheet {
  max-width: 1080px;
  margin: 0 auto;
  background: var(--paper);
  padding: 40px 44px 56px;
  border: 1px solid var(--line);
}
header.doc { border-bottom: 3px solid var(--head); padding-bottom: 18px; }
header.doc h1 { margin: 0 0 6px; font-size: 27px; line-height: 1.2; }
header.doc .sub { color: var(--muted); font-size: 15px; }
header.doc .meta { color: var(--muted); font-size: 13px; margin-top: 10px; }
nav.toc { margin: 24px 0 8px; padding: 14px 18px; background: var(--wash);
          border: 1px solid var(--line); }
nav.toc h2 { margin: 0 0 8px; font-size: 13px; letter-spacing: .08em;
             text-transform: uppercase; color: var(--muted); }
nav.toc ol { margin: 0; padding-left: 20px; columns: 2; column-gap: 32px; }
nav.toc a { color: var(--ink); }
section.sec { margin-top: 34px; page-break-inside: auto; }
section.sec > h2 {
  margin: 0 0 14px; font-size: 20px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
h3.cap { margin: 22px 0 8px; font-size: 14px; letter-spacing: .06em;
         text-transform: uppercase; color: var(--muted); }
p.para { margin: 0 0 12px; max-width: 78ch; }
dl.kv { display: grid; grid-template-columns: minmax(180px, 34%) 1fr; gap: 0;
        margin: 0 0 14px; border-top: 1px solid var(--line); }
dl.kv > div { display: contents; }
dl.kv dt, dl.kv dd {
  margin: 0; padding: 7px 10px; border-bottom: 1px solid var(--line);
}
dl.kv dt { color: var(--muted); }
dl.kv dd { font-variant-numeric: tabular-nums; }
dl.kv dd .note { display: block; color: var(--muted); font-size: 12.5px;
                 margin-top: 3px; font-variant-numeric: normal; }
.scroll { overflow-x: auto; margin: 0 0 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px;
        page-break-inside: auto; }
caption { caption-side: top; text-align: left; margin: 18px 0 8px; font-size: 14px;
          letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
th, td { border: 1px solid var(--line); padding: 6px 9px; vertical-align: top;
         text-align: left; }
thead th { background: var(--head); color: #fff; font-weight: 600; position: sticky;
           top: 0; }
tbody tr:nth-child(even) td { background: var(--wash); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.ctr, th.ctr { text-align: center; }
td.em { font-weight: 700; }
tr { page-break-inside: avoid; }
p.note { margin: 4px 0 16px; color: var(--muted); font-size: 12.5px; max-width: 92ch; }
p.empty { margin: 4px 0 16px; color: var(--muted); font-style: italic; }
.callout { margin: 0 0 16px; padding: 12px 14px; border-left: 4px solid var(--info);
           background: var(--info-bg); page-break-inside: avoid; }
.callout.warning { border-color: var(--warn); background: var(--warn-bg); }
.callout.method { border-color: var(--method); background: var(--method-bg); }
.callout .lab { font-size: 11.5px; letter-spacing: .1em; text-transform: uppercase;
                color: var(--muted); }
.callout h4 { margin: 2px 0 5px; font-size: 15px; }
.callout p { margin: 0; max-width: 82ch; }
table.matrix td.cell { text-align: center; min-width: 92px; }
table.matrix td.cell .n { font-weight: 700; font-size: 15px; }
table.matrix td.cell .codes { display: block; font-size: 11px; color: var(--ink);
                              margin-top: 3px; word-break: break-word; }
table.matrix th.rowhead { background: var(--wash); font-weight: 600; }
ul.legend { list-style: none; display: flex; flex-wrap: wrap; gap: 10px 18px;
            padding: 0; margin: 10px 0 6px; font-size: 12.5px; }
ul.legend li { display: flex; align-items: center; gap: 7px; }
ul.legend .sw { width: 15px; height: 15px; border: 1px solid var(--line);
                display: inline-block; }
footer.doc { margin-top: 40px; padding-top: 12px; border-top: 1px solid var(--line);
             color: var(--muted); font-size: 12px; }
@media (max-width: 720px) {
  body { padding: 12px 8px 40px; }
  .sheet { padding: 20px 16px 32px; }
  dl.kv { grid-template-columns: 1fr; }
  dl.kv dt { padding-bottom: 0; border-bottom: 0; }
  nav.toc ol { columns: 1; }
}
@media print {
  @page { size: A4; margin: 16mm; }
  body { background: #fff; padding: 0; font-size: 10.5pt; }
  .sheet { border: 0; padding: 0; max-width: none; }
  nav.toc { break-after: page; }
  section.sec { break-before: auto; }
  section.sec > h2 { break-after: avoid; }
  thead th { position: static; }
  .callout, tr, dl.kv > div { break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


def _esc(value: object) -> str:
    return (
        str("" if value is None else value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _align_class(column: Column) -> str:
    if column.align == "right" or column.format in {"int", "currency", "days", "pct",
                                                    "ratio"}:
        return "num"
    if column.align == "center":
        return "ctr"
    return ""


def _cell_html(cell: Cell, column: Column, currency: str) -> str:
    text = cell.display if cell.display is not None else format_value(
        cell.value, column.format, currency
    )
    classes = [_align_class(column)]
    if cell.emphasis:
        classes.append("em")
    attrs = f' class="{" ".join(c for c in classes if c)}"' if any(classes) else ""
    style = f' style="background:{_esc(cell.color)}"' if cell.color else ""
    return f"<td{attrs}{style}>{_esc(text)}</td>"


def _table_html(block: Table, currency: str) -> str:
    if not block.rows:
        head = f"<h3 class=\"cap\">{_esc(block.caption)}</h3>" if block.caption else ""
        return f'{head}<p class="empty">{_esc(block.empty_text)}</p>'

    caption = f"<caption>{_esc(block.caption)}</caption>" if block.caption else ""
    header = "".join(
        f'<th scope="col" class="{_align_class(col)}">{_esc(col.label)}</th>'
        for col in block.columns
    )
    body = []
    for row in block.rows:
        cells = "".join(
            _cell_html(cell, block.columns[i] if i < len(block.columns) else Column(
                label=""
            ), currency)
            for i, cell in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    note = f'<p class="note">{_esc(block.note)}</p>' if block.note else ""
    return (
        f'<div class="scroll"><table>{caption}<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>{note}'
    )


def _kv_html(block: KeyValues) -> str:
    caption = f'<h3 class="cap">{_esc(block.caption)}</h3>' if block.caption else ""
    rows = []
    for item in block.items:
        note = f'<span class="note">{_esc(item.note)}</span>' if item.note else ""
        rows.append(
            f"<div><dt>{_esc(item.label)}</dt>"
            f"<dd>{_esc(item.value)}{note}</dd></div>"
        )
    return f'{caption}<dl class="kv">{"".join(rows)}</dl>'


def _callout_html(block: Callout) -> str:
    label = _TONE_LABEL.get(block.tone, "Note")
    return (
        f'<aside class="callout {_esc(block.tone)}">'
        f'<span class="lab">{_esc(label)}</span>'
        f"<h4>{_esc(block.title)}</h4><p>{_esc(block.text)}</p></aside>"
    )


def _matrix_html(block: MatrixBlock) -> str:
    caption = f"<caption>{_esc(block.caption)}</caption>" if block.caption else ""
    header = '<th scope="col">Probability \\ Impact</th>' + "".join(
        f'<th scope="col">{level.level} · {_esc(level.label)}</th>'
        for level in block.impact_levels
    )
    rows = []
    for prob in block.probability_levels:
        cells = [
            f'<th scope="row" class="rowhead">{prob.level} · {_esc(prob.label)}</th>'
        ]
        for imp in block.impact_levels:
            cell = block.cell(prob.level, imp.level)
            if cell is None or cell.count == 0:
                cells.append('<td class="cell"></td>')
                continue
            codes = ", ".join(cell.codes[:6])
            if len(cell.codes) > 6:
                codes += f" +{len(cell.codes) - 6} more"
            title = _esc(f"{cell.band or 'no band'} · score {cell.score}")
            cells.append(
                f'<td class="cell" style="background:{_esc(cell.color)}" title="{title}">'
                f'<span class="n">{cell.count}</span>'
                f'<span class="codes">{_esc(codes)}</span></td>'
            )
        rows.append(f'<tr>{"".join(cells)}</tr>')

    legend = "".join(
        f'<li><span class="sw" style="background:{_esc(band.color)}"></span>'
        f"{_esc(band.name)} · {band.min_score}–{band.max_score}</li>"
        for band in block.bands
    )
    note = f'<p class="note">{_esc(block.note)}</p>' if block.note else ""
    return (
        f'<div class="scroll"><table class="matrix">{caption}'
        f'<thead><tr>{header}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'<ul class="legend">{legend}</ul>{note}'
    )


def _section_html(section: Section, currency: str) -> str:
    parts = [f'<section class="sec" id="{_esc(section.id)}">',
             f"<h2>{_esc(section.title)}</h2>"]
    for block in section.blocks:
        if isinstance(block, Paragraph):
            parts.append(f'<p class="para">{_esc(block.text)}</p>')
        elif isinstance(block, KeyValues):
            parts.append(_kv_html(block))
        elif isinstance(block, Table):
            parts.append(_table_html(block, currency))
        elif isinstance(block, Callout):
            parts.append(_callout_html(block))
        elif isinstance(block, MatrixBlock):
            parts.append(_matrix_html(block))
    parts.append("</section>")
    return "".join(parts)


def render_html(document: Document) -> str:
    """The whole document as one self-contained HTML file."""
    body = [_section_html(section, document.currency) for section in document.sections]
    toc = "".join(
        f'<li><a href="#{_esc(section.id)}">{_esc(section.title)}</a></li>'
        for section in document.sections
    )
    prepared = (
        f"Prepared by {_esc(document.prepared_by)} · " if document.prepared_by else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(document.title)}</title>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="sheet">'
        '<header class="doc">'
        f"<h1>{_esc(document.title)}</h1>"
        f'<div class="sub">{_esc(document.subtitle)}</div>'
        f'<div class="meta">{prepared}{_esc(document.generated_on.isoformat())}</div>'
        "</header>"
        f'<nav class="toc" aria-label="Contents"><h2>Contents</h2><ol>{toc}</ol></nav>'
        f'{"".join(body)}'
        '<footer class="doc">Generated by the iPype Risk Platform. Figures are Monte '
        "Carlo estimates; the basis section states the seed, the iteration count and "
        "everything excluded from the run.</footer>"
        "</div></body></html>"
    )
